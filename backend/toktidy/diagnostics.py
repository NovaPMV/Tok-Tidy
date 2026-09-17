"""Installation checks, shared by `toktidy doctor` and the app's Diagnostics tab."""
from __future__ import annotations

import platform
import sqlite3
import sys

from . import __version__
from .config import ConfigError, load_settings


def run_checks(test_whisper: bool = False) -> list[tuple[str, str]]:
    """Returns (status, message) pairs; status is OK, FAIL or NOTE."""
    out: list[tuple[str, str]] = []
    add = lambda st, msg: out.append((st, msg))  # noqa: E731

    add("NOTE", f"TokTidy {__version__} | Python {sys.version.split()[0]} | {platform.platform()}")
    from . import media
    try:
        media.tool("ffmpeg")
        media.tool("ffprobe")
        add("OK", f"ffmpeg found: {media.tool('ffmpeg')}")
        if media.nvenc_available():
            add("OK", "NVIDIA encoder (NVENC) works - fast previews")
        else:
            add("NOTE", "NVENC not available - previews use the CPU encoder (slower, still fine)")
    except ConfigError as e:
        add("FAIL", str(e))

    try:
        import torch
        cuda = torch.cuda.is_available()
        add("OK" if cuda else "FAIL", f"PyTorch {torch.__version__}, CUDA available: {cuda}")
        if cuda:
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / 2**30
            x = torch.ones(256, 256, device="cuda") @ torch.ones(256, 256, device="cuda")
            good = float(x[0, 0]) == 256.0
            add("OK" if good else "FAIL", f"GPU: {name} ({vram:.1f} GB), compute test {'passed' if good else 'FAILED'}")
    except ImportError:
        add("FAIL", "PyTorch is not installed")
    except Exception as e:
        add("FAIL", f"GPU test failed: {e}")

    for mod, label in (("transformers", "transformers (SigLIP 2)"), ("open_clip", "open_clip (OpenCLIP)"),
                       ("faster_whisper", "faster-whisper"), ("fastapi", "fastapi"),
                       ("uvicorn", "uvicorn"), ("rapidfuzz", "rapidfuzz"), ("psutil", "psutil")):
        try:
            m = __import__(mod)
            add("OK", f"{label} {getattr(m, '__version__', '')}")
        except Exception as e:
            add("FAIL", f"{label} could not be loaded ({type(e).__name__}: {e})")

    c = sqlite3.connect(":memory:")
    try:
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        add("OK", f"SQLite {sqlite3.sqlite_version} with full-text search")
    except sqlite3.OperationalError:
        add("NOTE", "SQLite has no FTS5; audio search uses slower matching")

    try:
        from .cache import get_paths
        paths = get_paths()
        add("OK", f"Library: {paths.database}")
        add("NOTE", f"Temp folder: {paths.temp}")
    except ConfigError as e:
        add("NOTE", str(e).splitlines()[0])

    if test_whisper:
        try:
            from .models import Transcriber
            t = Transcriber(load_settings()["transcription"], log=lambda m: add("NOTE", m.strip()))
            add("OK" if t.device == "cuda" else "NOTE", f"Whisper loaded on {t.device}")
            t.unload()
        except Exception as e:
            add("FAIL", f"Whisper failed: {type(e).__name__}: {e}")
    return out
