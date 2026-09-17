"""Everything that touches ffmpeg.

Each clip is read from the hard drive ONCE. A single ffmpeg run produces, as
needed:
  * sampled frames (1 every 2 s) for AI search and colour analysis
  * a full-length, muted 240p H.264 preview loop
  * tiny greyscale frames for motion / pace scores
  * 16 kHz mono audio for transcription (written to the temp folder)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image

from .config import ConfigError


class MediaError(Exception):
    pass


_tool_cache: dict[str, str] = {}
_lock = threading.Lock()
_encoder_state: dict[str, bool] = {}


def tool(name: str) -> str:
    if name not in _tool_cache:
        found = shutil.which(name)
        if not found:
            raise ConfigError(
                f"{name} was not found. Install ffmpeg and add its 'bin' folder to PATH "
                "(see README, step 4)."
            )
        _tool_cache[name] = found
    return _tool_cache[name]


def _popen_flags(low_priority: bool) -> dict:
    if os.name != "nt":
        return {}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP  # Ctrl+C in the console won't kill ffmpeg mid-clip
    flags |= subprocess.CREATE_NO_WINDOW
    if low_priority:
        flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return {"creationflags": flags}


def _run(cmd, low_priority=True, timeout=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, timeout=timeout, **_popen_flags(low_priority),
    )


def _err_tail(stderr: bytes, lines: int = 4) -> str:
    text = stderr.decode("utf-8", errors="replace").strip().splitlines()
    return " | ".join(text[-lines:]) if text else "unknown ffmpeg error"


# ------------------------------------------------------------ probing

@dataclass
class ProbeInfo:
    duration: float
    width: int           # as displayed (rotation applied)
    height: int
    fps: float
    vcodec: str
    has_audio: bool


def _parse_rate(s: str | None) -> float:
    if not s or s in ("0/0", "0"):
        return 0.0
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path, low_priority=True) -> ProbeInfo:
    cmd = [tool("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    res = _run(cmd, low_priority, timeout=60)
    if res.returncode != 0:
        raise MediaError(f"ffprobe failed: {_err_tail(res.stderr)}")
    data = json.loads(res.stdout.decode("utf-8", errors="replace") or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise MediaError("no video stream")
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    if not w or not h:
        raise MediaError("could not read video size")
    rotation = 0
    try:
        rotation = int(float(video.get("tags", {}).get("rotate", 0)))
    except ValueError:
        pass
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    if abs(rotation) % 180 == 90:
        w, h = h, w
    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))
    if fps > 240:  # bogus container values
        fps = _parse_rate(video.get("r_frame_rate"))
    duration = 0.0
    for src in (data.get("format", {}).get("duration"), video.get("duration")):
        try:
            duration = float(src)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    return ProbeInfo(
        duration=duration, width=w, height=h, fps=round(fps, 3),
        vcodec=video.get("codec_name", "?"),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


# ------------------------------------------------------------ encoders

def nvenc_available() -> bool:
    with _lock:
        if "nvenc" not in _encoder_state:
            cmd = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                   "-i", "color=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"]
            try:
                _encoder_state["nvenc"] = _run(cmd, timeout=30).returncode == 0
            except Exception:
                _encoder_state["nvenc"] = False
        return _encoder_state["nvenc"]


def _disable_nvenc():
    with _lock:
        _encoder_state["nvenc"] = False


def choose_encoder(setting: str) -> str:
    if setting == "x264":
        return "x264"
    if setting == "nvenc":
        return "nvenc" if nvenc_available() else "x264"
    return "nvenc" if nvenc_available() else "x264"


def _encoder_args(encoder: str, quality: int) -> list[str]:
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(quality),
                "-b:v", "0", "-maxrate", "1500k", "-bufsize", "3M", "-profile:v", "main"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality), "-profile:v", "main"]


# ------------------------------------------------------------ sizing helpers

def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def fit_long_side(w: int, h: int, target: int) -> tuple[int, int]:
    scale = min(1.0, target / max(w, h))
    return _even(w * scale), _even(h * scale)


def fit_short_side(w: int, h: int, target: int) -> tuple[int, int]:
    scale = min(1.0, target / min(w, h))
    return _even(w * scale), _even(h * scale)


# ------------------------------------------------------------ the decode job

@dataclass
class DecodeRequest:
    src: Path
    info: ProbeInfo
    want_frames: bool
    want_analysis: bool
    want_preview: bool
    want_audio: bool
    preview_out: Path | None
    tmp_prefix: Path        # e.g. %TEMP%\TokTidy\123
    settings: dict


@dataclass
class DecodeOutput:
    frames: list[np.ndarray] | None = None     # RGB uint8, one per sampled frame
    frame_times: list[float] = field(default_factory=list)
    gray: np.ndarray | None = None             # (N, h, w) uint8 at motion_fps
    audio_path: Path | None = None
    preview_written: bool = False


def run_decode(req: DecodeRequest) -> DecodeOutput:
    s = req.settings["indexing"]
    low = s.get("low_priority", True)
    info = req.info
    need_rgb = req.want_frames or req.want_analysis
    need_gray = req.want_analysis

    interval = float(s["frame_interval"])
    fw, fh = fit_long_side(info.width, info.height, int(s["frame_max_side"]))
    gw, gh = fit_long_side(info.width, info.height, 64)
    pw, ph = fit_short_side(info.width, info.height, int(s["preview_short_side"]))
    rate = Fraction(1 / interval).limit_denominator(1000)
    motion_fps = int(s.get("motion_fps", 10))

    rgb_file = Path(f"{req.tmp_prefix}_frames.rgb")
    gray_file = Path(f"{req.tmp_prefix}_motion.gray")
    audio_file = Path(f"{req.tmp_prefix}_audio.flac")
    preview_tmp = None
    if req.want_preview and req.preview_out is not None:
        req.preview_out.parent.mkdir(parents=True, exist_ok=True)
        preview_tmp = req.preview_out.with_name(f".{req.preview_out.stem}.part.mp4")

    encoder = choose_encoder(s.get("preview_encoder", "auto"))
    hwaccel = bool(s.get("hwaccel_decode"))

    def build(encoder: str, hwaccel: bool) -> list[str]:
        branches = []  # (filter chain, output args)
        if need_rgb:
            branches.append((
                f"fps={rate.numerator}/{rate.denominator},scale={fw}:{fh}:flags=bicubic,format=rgb24",
                ["-pix_fmt", "rgb24", "-f", "rawvideo", str(rgb_file)],
            ))
        if need_gray:
            branches.append((
                f"fps={motion_fps},scale={gw}:{gh}:flags=area,format=gray",
                ["-pix_fmt", "gray", "-f", "rawvideo", str(gray_file)],
            ))
        if preview_tmp is not None:
            chain = ""
            max_fps = float(s.get("preview_max_fps", 30))
            if info.fps and info.fps > max_fps + 0.5:
                chain += f"fps={max_fps:g},"
            chain += f"scale={pw}:{ph}:flags=bicubic,setsar=1,format=yuv420p"
            branches.append((
                chain,
                _encoder_args(encoder, int(s.get("preview_quality", 28)))
                + ["-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", "-f", "mp4", str(preview_tmp)],
            ))
        cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
        if hwaccel:
            cmd += ["-hwaccel", "cuda"]
        cmd += ["-i", str(req.src)]
        if branches:
            n = len(branches)
            graph = f"[0:v:0]split={n}" + "".join(f"[s{i}]" for i in range(n))
            for i, (chain, _) in enumerate(branches):
                graph += f";[s{i}]{chain}[o{i}]"
            cmd += ["-filter_complex", graph]
            for i, (_, out_args) in enumerate(branches):
                cmd += ["-map", f"[o{i}]"] + out_args
        if req.want_audio and info.has_audio:
            cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(audio_file)]
        return cmd

    timeout = max(180, int(info.duration * 30))
    attempts = [(encoder, hwaccel)]
    if hwaccel:
        attempts.append((encoder, False))
    if encoder == "nvenc":
        attempts.append(("x264", False))

    last_err = ""
    try:
        for enc, hw in attempts:
            try:
                res = _run(build(enc, hw), low, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise MediaError(f"ffmpeg timed out after {timeout}s")
            if res.returncode == 0:
                break
            last_err = _err_tail(res.stderr)
            if enc == "nvenc" and "nvenc" in last_err.lower():
                _disable_nvenc()
        else:
            raise MediaError(f"ffmpeg failed: {last_err}")

        out = DecodeOutput()
        if need_rgb:
            raw = np.fromfile(rgb_file, dtype=np.uint8) if rgb_file.exists() else np.zeros(0, np.uint8)
            n = raw.size // (fw * fh * 3)
            if n == 0:
                raw = _single_frame(req.src, fw, fh, low)
                n = raw.size // (fw * fh * 3)
            if n == 0:
                raise MediaError("no frames could be decoded")
            frames = raw[: n * fw * fh * 3].reshape(n, fh, fw, 3)
            out.frames = [frames[i] for i in range(n)]
            out.frame_times = [round(i * interval, 3) for i in range(n)]
        if need_gray and gray_file.exists():
            g = np.fromfile(gray_file, dtype=np.uint8)
            n = g.size // (gw * gh)
            out.gray = g[: n * gw * gh].reshape(n, gh, gw)
        if preview_tmp is not None:
            if not preview_tmp.exists() or preview_tmp.stat().st_size == 0:
                raise MediaError("preview was not written")
            os.replace(preview_tmp, req.preview_out)
            out.preview_written = True
        if req.want_audio and audio_file.exists() and audio_file.stat().st_size > 0:
            out.audio_path = audio_file
        return out
    finally:
        rgb_file.unlink(missing_ok=True)
        gray_file.unlink(missing_ok=True)
        if preview_tmp is not None:
            preview_tmp.unlink(missing_ok=True)


def _single_frame(src: Path, w: int, h: int, low: bool) -> np.ndarray:
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(src),
           "-frames:v", "1", "-vf", f"scale={w}:{h},format=rgb24",
           "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    res = _run(cmd, low, timeout=120)
    return np.frombuffer(res.stdout, dtype=np.uint8) if res.returncode == 0 else np.zeros(0, np.uint8)


# ------------------------------------------------------------ analysis

def _signature(frame: np.ndarray) -> np.ndarray:
    img = Image.fromarray(frame).convert("L").resize((32, 32), Image.BILINEAR)
    return np.asarray(img, dtype=np.float32) / 255.0


def dedupe_frames(frames, times, threshold: float):
    """Drop frames that are nearly identical to the previously kept frame."""
    if threshold <= 0 or len(frames) <= 1:
        return list(range(len(frames)))
    keep = [0]
    last = _signature(frames[0])
    for i in range(1, len(frames)):
        sig = _signature(frames[i])
        if float(np.abs(sig - last).mean()) >= threshold:
            keep.append(i)
            last = sig
    return keep


def save_frames(frames, indices, out_dir: Path, quality: int) -> None:
    part = out_dir.with_name(out_dir.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True, exist_ok=True)
    for new_idx, i in enumerate(indices):
        Image.fromarray(frames[i]).save(part / f"{new_idx:03d}.jpg", quality=quality, optimize=True)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.replace(part, out_dir)


def brightness(frames) -> float:
    lum = [
        (f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114).mean()
        for f in frames
    ]
    return round(float(np.mean(lum)) / 255.0, 4)


def dominant_colors(frames, k: int = 5, samples: int = 4000, iters: int = 12):
    rng = np.random.default_rng(0)
    pix = np.concatenate([f.reshape(-1, 3) for f in frames])
    take = rng.choice(len(pix), size=min(samples, len(pix)), replace=False)
    X = pix[take].astype(np.float32)
    k = min(k, len(X))
    C = X[rng.choice(len(X), size=k, replace=False)].copy()
    labels = np.zeros(len(X), np.int64)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        labels = d.argmin(1)
        for j in range(k):
            m = labels == j
            if m.any():
                C[j] = X[m].mean(0)
    weights = np.bincount(labels, minlength=k) / len(X)
    order = np.argsort(-weights)
    return [
        [int(C[j, 0]), int(C[j, 1]), int(C[j, 2]), round(float(weights[j]), 4)]
        for j in order if weights[j] > 0
    ]


def motion_scores(gray: np.ndarray | None, duration: float) -> tuple[float, float]:
    """Returns (motion 0..1, pace = hard cuts per minute)."""
    if gray is None or len(gray) < 2:
        return 0.0, 0.0
    diffs = np.abs(np.diff(gray.astype(np.int16), axis=0)).mean(axis=(1, 2)) / 255.0
    med = float(np.median(diffs))
    cut_threshold = max(0.15, med * 5)
    cuts = diffs > cut_threshold
    calm = diffs[~cuts]
    motion = float(calm.mean()) if calm.size else float(diffs.mean())
    minutes = max(duration, len(gray) / 10.0, 0.5) / 60.0
    return round(min(1.0, motion * 4), 4), round(float(cuts.sum()) / minutes, 2)
