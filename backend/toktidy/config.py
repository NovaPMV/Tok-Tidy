"""Settings for TokTidy.

Settings live in %APPDATA%\\TokTidy\\settings.json. Any key missing from
that file falls back to the defaults below, so new options added in later
versions appear automatically.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

APP_NAME = "TokTidy"
BOTH = "both"            # pseudo model key: search with every enabled model and merge the rankings

VIDEO_EXTENSIONS = [
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".flv",
    ".mpg", ".mpeg", ".3gp", ".ts", ".mts", ".m2ts",
]

DEFAULTS: dict = {
    # Where the cache lives. Filled in by `toktidy init`.
    "paths": {
        "database": None,     # full path to library.db
        "frames": None,       # folder
        "previews": None,     # folder
        "embeddings": None,   # folder
        "temp": None,         # scratch space; None = Windows %TEMP% (usually your SSD)
    },
    "indexing": {
        "frame_interval": 2.0,        # seconds between sampled frames
        "frame_max_side": 384,        # saved frame size (longest side, px)
        "frame_jpeg_quality": 85,
        "dedupe_threshold": 0.02,     # skip frames nearly identical to the previous kept frame (0 = keep all)
        "preview_short_side": 240,    # 240p previews
        "preview_max_fps": 30,
        "preview_encoder": "auto",    # auto | nvenc | x264
        "preview_quality": 28,        # lower = better quality, bigger files
        "decode_workers": 1,          # 1 is best when originals and cache share one HDD
        "hwaccel_decode": False,      # try NVIDIA decoding (falls back automatically)
        "low_priority": True,         # keep the PC responsive while indexing
        "embed_batch_size": 32,
        "gpu_models_at_once": 1,      # 1 is safest for 6 GB of VRAM
        "precision": "auto",          # auto | fp16 | fp32
        "motion_fps": 10,
    },
    "models": {
        "siglip2": {
            "enabled": True,
            "label": "SigLIP 2",
            "backend": "transformers",
            "model_id": "google/siglip2-so400m-patch14-224",
            "text_template": "a photo of {}.",
        },
        "openclip": {
            "enabled": True,
            "label": "OpenCLIP",
            "backend": "open_clip",
            "model_name": "ViT-L-14",
            "pretrained": "datacomp_xl_s13b_b90k",
            "text_template": "a photo of {}.",
        },
    },
    "default_model": "openclip",      # a model key, or "both" to combine every enabled model
    # Search tuning
    "search": {
        # Search each phrase several ways ("a photo of ...", "a video still of ...")
        # and average the results. Costs nothing at indexing time.
        "multi_phrasing": True,
        "phrasings": [
            "a photo of {}.",
            "a picture of {}.",
            "a video still of {}.",
            "a tiktok video showing {}.",
            "{}",
        ],
    },
    "transcription": {
        "enabled": True,
        "model_size": "small",        # tiny | base | small | medium  (multilingual)
        "device": "auto",             # auto | cuda | cpu
        "compute_type": "auto",
        "vad": True,                  # skip silence / instrumental sections
        "beam_size": 5,
    },
    "video_extensions": VIDEO_EXTENSIONS,
    # Desktop app preferences
    "app": {
        "show_details": True,
        "auto_mark_used": True,
        "page_size": 500,
        "tile_width": 170,
        "sort": "name",
        "sort_desc": False,
        "hide_used": False,
        "unload_models_after_min": 10,   # free VRAM for Premiere when search sits idle
        "max_playing": 48,               # most previews allowed to play at once
    },
}


class ConfigError(Exception):
    """A problem the user needs to fix (shown without a traceback)."""


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return app_dir() / "settings.json"


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_settings() -> dict:
    data = {}
    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"settings.json is not valid JSON ({e}). File: {path}")
    return _deep_merge(copy.deepcopy(DEFAULTS), data)


def save_settings(settings: dict) -> None:
    path = settings_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def enabled_models(settings: dict) -> list[str]:
    return [k for k, m in settings["models"].items() if m.get("enabled")]


def resolve_models(settings: dict, key: str | None = None) -> list[str]:
    """Model keys to search with. "both" means every enabled model."""
    key = key or settings.get("default_model") or BOTH
    if key == BOTH:
        keys = enabled_models(settings)
        if not keys:
            raise ConfigError("No search model is turned on. Enable one in Settings > Search & AI.")
        return keys
    if key not in settings["models"]:
        raise ConfigError(f"Unknown model '{key}'. Choose from: {', '.join(settings['models'])}, {BOTH}")
    return [key]
