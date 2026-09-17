"""AI models used for search and transcription.

* SigLIP 2 and OpenCLIP turn frames and search text into comparable vectors.
* faster-whisper transcribes speech.

The ModelManager keeps only `gpu_models_at_once` models in VRAM (default 1),
which keeps a 6 GB card like the GTX 1660 Super out of memory trouble.
"""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .config import ConfigError


def _torch():
    try:
        import torch
    except ImportError as e:
        raise ConfigError("PyTorch is not installed. Run setup.bat again.") from e
    return torch


def pad_square(img: Image.Image) -> Image.Image:
    """Letterbox to a square so vertical videos are never cropped (heads and shoes stay in)."""
    img = img.convert("RGB")
    w, h = img.size
    if w == h:
        return img
    s = max(w, h)
    canvas = Image.new("RGB", (s, s), (0, 0, 0))
    canvas.paste(img, ((s - w) // 2, (s - h) // 2))
    return canvas


def _as_tensor(out):
    torch = _torch()
    if isinstance(out, torch.Tensor):
        return out
    for attr in ("image_embeds", "text_embeds", "pooler_output"):
        v = getattr(out, attr, None)
        if v is not None:
            return v
    return out[0]


def _test_image() -> Image.Image:
    x = np.linspace(0, 255, 224, dtype=np.uint8)
    arr = np.stack(np.meshgrid(x, x[::-1]), -1)
    arr = np.concatenate([arr, arr[..., :1]], -1)
    return Image.fromarray(arr.astype(np.uint8))


def model_fingerprint(key: str, cfg: dict) -> str:
    if cfg["backend"] == "transformers":
        return f"hf:{cfg['model_id']}"
    if cfg["backend"] == "open_clip":
        return f"open_clip:{cfg['model_name']}:{cfg['pretrained']}"
    raise ConfigError(f"Model '{key}' has an unknown backend '{cfg['backend']}'")


class Embedder:
    key: str
    label: str
    fingerprint: str
    precision: str

    def encode_images(self, images: list[Image.Image]) -> np.ndarray: ...
    def encode_texts(self, texts: list[str], template: str | None = None) -> np.ndarray: ...

    def encode_queries(self, texts: list[str], templates: list[str] | None = None) -> np.ndarray:
        """Search phrases -> vectors. With several templates, each phrase is
        written several ways and the vectors are averaged (prompt ensembling)."""
        if not templates:
            return self.encode_texts(texts)
        per_template = [self.encode_texts(texts, template=tpl) for tpl in templates]
        v = np.mean(np.stack(per_template), axis=0)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)
        return v.astype(np.float32)

    def _finish(self, feats) -> np.ndarray:
        torch = _torch()
        feats = _as_tensor(feats).float()
        feats = torch.nn.functional.normalize(feats, dim=-1)
        return feats.cpu().numpy().astype(np.float32)

    def _sane(self) -> bool:
        try:
            a = self.encode_images([_test_image()])
            b = self.encode_texts(["a test"])
            return bool(np.isfinite(a).all() and np.isfinite(b).all() and abs(np.linalg.norm(a) - 1) < 0.01)
        except Exception:
            return False

    def unload(self):
        for attr in ("model", "processor", "preprocess", "tokenizer"):
            if hasattr(self, attr):
                setattr(self, attr, None)


class SigLIPEmbedder(Embedder):
    def __init__(self, key, cfg, device, dtype_name):
        torch = _torch()
        try:
            from transformers import AutoModel, AutoProcessor
        except ImportError as e:
            raise ConfigError("transformers is not installed. Run setup.bat again.") from e
        self.key, self.label = key, cfg.get("label", key)
        self.fingerprint = model_fingerprint(key, cfg)
        self.template = cfg.get("text_template") or "{}"
        self.device = device
        self.dtype = torch.float16 if dtype_name == "fp16" else torch.float32
        self.precision = dtype_name
        self.processor = AutoProcessor.from_pretrained(cfg["model_id"])
        self.model = AutoModel.from_pretrained(cfg["model_id"], torch_dtype=self.dtype).to(device).eval()

    def encode_images(self, images):
        torch = _torch()
        inputs = self.processor(images=[pad_square(i) for i in images], return_tensors="pt")
        pv = inputs["pixel_values"].to(self.device, self.dtype)
        with torch.inference_mode():
            return self._finish(self.model.get_image_features(pixel_values=pv))

    def encode_texts(self, texts, template=None):
        torch = _torch()
        tpl = template or self.template
        texts = [tpl.format(t).lower() for t in texts]
        inputs = self.processor(text=texts, padding="max_length", max_length=64,
                                truncation=True, return_tensors="pt")
        with torch.inference_mode():
            return self._finish(self.model.get_text_features(input_ids=inputs["input_ids"].to(self.device)))


class OpenCLIPEmbedder(Embedder):
    def __init__(self, key, cfg, device, dtype_name):
        torch = _torch()
        try:
            import open_clip
        except ImportError as e:
            raise ConfigError("open_clip_torch is not installed. Run setup.bat again.") from e
        self.key, self.label = key, cfg.get("label", key)
        self.fingerprint = model_fingerprint(key, cfg)
        self.template = cfg.get("text_template") or "{}"
        self.device = device
        self.precision = dtype_name
        self.dtype = torch.float16 if dtype_name == "fp16" else torch.float32
        model, _, preprocess = open_clip.create_model_and_transforms(
            cfg["model_name"], pretrained=cfg["pretrained"], device=device
        )
        self.model = model.to(self.dtype).eval()
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(cfg["model_name"])

    def encode_images(self, images):
        torch = _torch()
        x = torch.stack([self.preprocess(pad_square(i)) for i in images]).to(self.device, self.dtype)
        with torch.inference_mode():
            return self._finish(self.model.encode_image(x))

    def encode_texts(self, texts, template=None):
        torch = _torch()
        tpl = template or self.template
        tokens = self.tokenizer([tpl.format(t) for t in texts]).to(self.device)
        with torch.inference_mode():
            return self._finish(self.model.encode_text(tokens))


def load_embedder(key: str, cfg: dict, precision: str = "auto", log=print) -> Embedder:
    torch = _torch()
    cls = {"transformers": SigLIPEmbedder, "open_clip": OpenCLIPEmbedder}.get(cfg["backend"])
    if cls is None:
        raise ConfigError(f"Model '{key}' has an unknown backend '{cfg['backend']}'")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        log(f"  ! No CUDA GPU detected; {cfg.get('label', key)} will run on the CPU (slow).")
        return cls(key, cfg, device, "fp32")
    if precision in ("fp16", "auto"):
        m = cls(key, cfg, device, "fp16")
        if precision == "fp16" or m._sane():
            return m
        log(f"  ! {m.label} gave invalid results in half precision on this GPU; using full precision.")
        m.unload()
        del m
        free_vram()
    return cls(key, cfg, device, "fp32")


def free_vram():
    gc.collect()
    try:
        torch = _torch()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ConfigError:
        pass


# ------------------------------------------------------------ Whisper

def _add_cuda_dll_dirs():
    """faster-whisper needs cuBLAS/cuDNN DLLs; PyTorch already ships them."""
    if os.name != "nt":
        return
    dirs = []
    try:
        import torch
        dirs.append(Path(torch.__file__).parent / "lib")
    except ImportError:
        pass
    for sp in sys.path:
        nv = Path(sp) / "nvidia"
        if nv.is_dir():
            dirs += [p / "bin" for p in nv.iterdir() if (p / "bin").is_dir()]
    for d in dirs:
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    key = "whisper"

    def __init__(self, cfg: dict, log=print):
        self.cfg, self.log = cfg, log
        _add_cuda_dll_dirs()
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError as e:
            raise ConfigError("faster-whisper is not installed. Run setup.bat again.") from e
        device = cfg.get("device", "auto")
        if device == "auto":
            try:
                device = "cuda" if _torch().cuda.is_available() else "cpu"
            except ConfigError:
                device = "cpu"
        self._load(device)

    def _load(self, device):
        from faster_whisper import WhisperModel
        compute = self.cfg.get("compute_type", "auto")
        if compute == "auto":
            compute = "int8" if device == "cuda" else "int8"
        try:
            self.model = WhisperModel(self.cfg.get("model_size", "small"), device=device, compute_type=compute)
            self.device = device
        except Exception as e:
            if device != "cuda":
                raise
            self.log(f"  ! Whisper could not start on the GPU ({e}); using the CPU instead.")
            self._load("cpu")

    def transcribe(self, audio_path: Path) -> dict:
        try:
            return self._transcribe(audio_path)
        except RuntimeError as e:
            msg = str(e).lower()
            if self.device == "cuda" and any(k in msg for k in ("cublas", "cudnn", "library", "cuda")):
                self.log(f"  ! Whisper GPU error ({e}); switching to the CPU.")
                self._load("cpu")
                return self._transcribe(audio_path)
            raise

    def _transcribe(self, audio_path: Path) -> dict:
        segments, info = self.model.transcribe(
            str(audio_path),
            beam_size=int(self.cfg.get("beam_size", 5)),
            vad_filter=bool(self.cfg.get("vad", True)),
            condition_on_previous_text=False,
        )
        segs = []
        for s in segments:
            t = s.text.strip()
            if t:
                segs.append([round(s.start, 2), round(s.end, 2), t])
        text = " ".join(s[2] for s in segs)
        return {
            "text": text,
            "segments": segs,
            "language": info.language if text else None,
            "language_prob": round(float(info.language_probability), 3) if text else None,
        }

    def unload(self):
        self.model = None


# ------------------------------------------------------------ manager

class ModelManager:
    """Loads models on demand and limits how many sit in VRAM.

    Anything that uses a model must hold `lock` while getting AND using it,
    because another thread (search vs. indexing) may swap models otherwise.
    """

    def __init__(self, settings: dict, log=print):
        import threading
        self.settings, self.log = settings, log
        self.max_loaded = max(1, int(settings["indexing"].get("gpu_models_at_once", 1)))
        self.loaded: dict[str, object] = {}
        self.lock = threading.RLock()
        self.pair_fits = True   # becomes False if two models didn't fit in VRAM together
        import time
        self.last_used = time.time()

    def _make_room(self, keep: str, protect=()):
        limit = max(self.max_loaded, len(set(protect) | {keep}))
        while len(self.loaded) >= limit:
            victim = next((k for k in self.loaded if k != keep and k not in protect), None)
            if victim is None:
                break
            self.log(f"  Unloading {victim} to free GPU memory")
            self.loaded.pop(victim).unload()
            free_vram()

    def embedder(self, key: str, protect=()) -> Embedder:
        """`protect`: other models that should stay loaded next to this one
        (the "Both models" search keeps both in VRAM when they fit)."""
        if key not in self.loaded:
            cfg = self.settings["models"].get(key)
            if not cfg:
                raise ConfigError(f"Unknown model '{key}'. Known: {', '.join(self.settings['models'])}")
            protect = [k for k in protect if k != key and k in self.loaded] if self.pair_fits else []
            self._make_room(key, protect)
            self.log(f"  Loading {cfg.get('label', key)} (first run downloads it) ...")
            precision = self.settings["indexing"].get("precision", "auto")
            try:
                m = load_embedder(key, cfg, precision, self.log)
            except Exception as e:
                if not protect or "out of memory" not in str(e).lower():
                    raise
                # Both models don't fit in VRAM together: swap instead.
                self.log("  Not enough GPU memory for two models at once; swapping instead.")
                self.pair_fits = False
                free_vram()
                self._make_room(key)
                m = load_embedder(key, cfg, precision, self.log)
            self.log(f"  {m.label} ready ({m.precision}, {m.device})")
            self.loaded[key] = m
        # most-recently-used goes last
        import time
        self.last_used = time.time()
        self.loaded[key] = self.loaded.pop(key)
        return self.loaded[key]

    def transcriber(self) -> Transcriber:
        if "whisper" not in self.loaded:
            self._make_room("whisper")
            size = self.settings["transcription"].get("model_size", "small")
            self.log(f"  Loading Whisper ({size}) ...")
            t = Transcriber(self.settings["transcription"], self.log)
            self.log(f"  Whisper ready ({t.device})")
            self.loaded["whisper"] = t
        import time
        self.last_used = time.time()
        self.loaded["whisper"] = self.loaded.pop("whisper")
        return self.loaded["whisper"]

    def loaded_keys(self) -> list[str]:
        return list(self.loaded)

    def unload_all(self):
        with self.lock:
            for m in self.loaded.values():
                m.unload()
            self.loaded.clear()
            free_vram()

    def unload(self, key):
        with self.lock:
            m = self.loaded.pop(key, None)
            if m is not None:
                m.unload()
                free_vram()
