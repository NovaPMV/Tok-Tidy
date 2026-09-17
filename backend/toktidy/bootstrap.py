"""Helpers the TokTidy installer runs inside the new Python environment.

    python -m toktidy.bootstrap gpu-check
    python -m toktidy.bootstrap download-models --models siglip2,openclip,whisper

Output is plain, line-by-line text because the installer window shows it live.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from .config import DEFAULTS

# Approximate download sizes, only used for progress messages.
APPROX_GB = {"siglip2": 4.5, "openclip": 1.7, "whisper": 0.5}
def say(msg: str = "") -> None:
    print(msg, flush=True)


def _hub_dir() -> Path:
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(home) / "hub"


def _dir_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class Progress:
    """Prints how much has been downloaded every few seconds."""

    def __init__(self, label: str, approx_gb: float):
        self.label, self.approx = label, approx_gb
        self.stop = threading.Event()
        self.start_size = 0

    def __enter__(self):
        hub = _hub_dir()
        hub.mkdir(parents=True, exist_ok=True)
        self.start_size = _dir_size(hub)
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        return self

    def _run(self):
        last = -1.0
        while not self.stop.wait(8):
            gb = (_dir_size(_hub_dir()) - self.start_size) / 1e9
            if gb - last >= 0.05:
                last = gb
                pct = min(99, int(gb / self.approx * 100)) if self.approx else 0
                say(f"    {self.label}: {gb:.2f} GB of about {self.approx:.1f} GB ({pct}%)")

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join(timeout=2)


def download_siglip2(cfg: dict) -> None:
    from huggingface_hub import snapshot_download
    snapshot_download(cfg["model_id"], allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"])


def download_openclip(cfg: dict) -> None:
    try:
        from open_clip import pretrained as P
        pcfg = P.get_pretrained_cfg(cfg["model_name"], cfg["pretrained"])
        path = P.download_pretrained(pcfg)
        if path:
            return
    except Exception as e:  # API differences between open_clip versions
        say(f"  (falling back to a full model load: {type(e).__name__})")
    import open_clip
    open_clip.create_model_and_transforms(cfg["model_name"], pretrained=cfg["pretrained"], device="cpu")


def download_whisper(size: str) -> None:
    from faster_whisper.utils import download_model
    download_model(size)


def cmd_download_models(args) -> int:
    keys = [k.strip() for k in args.models.split(",") if k.strip()]
    say(f"  Models will be stored in {_hub_dir().parent}")
    failed = []
    for key in keys:
        label = {"siglip2": "SigLIP 2", "openclip": "OpenCLIP", "whisper": "Whisper (speech)"}.get(key, key)
        say(f"  Getting {label} (about {APPROX_GB.get(key, 0):.1f} GB; already-downloaded parts are skipped) ...")
        t = time.time()
        try:
            with Progress(label, APPROX_GB.get(key, 0)):
                if key == "siglip2":
                    download_siglip2(DEFAULTS["models"]["siglip2"])
                elif key == "openclip":
                    download_openclip(DEFAULTS["models"]["openclip"])
                elif key == "whisper":
                    download_whisper(DEFAULTS["transcription"]["model_size"])
                else:
                    say(f"  Unknown model '{key}', skipped.")
                    continue
            say(f"  {label} ready ({time.time() - t:.0f} s)")
        except Exception as e:
            say(f"  WARNING: {label} could not be downloaded now ({type(e).__name__}: {e}).")
            say("  TokTidy will download it the first time it is needed.")
            failed.append(key)
    return 0 if not failed else 2


def cmd_gpu_check(_args) -> int:
    try:
        import torch
    except Exception as e:
        say(f"  PyTorch could not be loaded: {e}")
        return 1
    say(f"  PyTorch {torch.__version__}")
    if not torch.cuda.is_available():
        say("  NVIDIA GPU: not available to PyTorch")
        return 2
    try:
        x = torch.randn(256, 256, device="cuda")
        float((x @ x).sum())
        y = x.half()
        float((y @ y).float().sum())
    except Exception as e:
        say(f"  NVIDIA GPU found but not usable with this PyTorch build: {e}")
        return 3
    say(f"  NVIDIA GPU ready: {torch.cuda.get_device_name(0)}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="toktidy.bootstrap")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download-models")
    d.add_argument("--models", default="siglip2,openclip,whisper")
    sub.add_parser("gpu-check")
    args = p.parse_args(argv)
    return {"download-models": cmd_download_models, "gpu-check": cmd_gpu_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
