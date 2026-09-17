"""Cache locations: database, frames, previews, embeddings.

Every cache folder contains a small `.toktidy-id.json` file carrying the
same cache id that is stored inside library.db. That lets the app confirm a
folder really belongs to this library when you relink it after moving.

Cache files are named by clip id (never by path), so moving your TikTok
folders or the cache itself never requires regenerating anything.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import ConfigError, load_settings, save_settings

ID_FILENAME = ".toktidy-id.json"
FOLDER_PARTS = ("frames", "previews", "embeddings")
ALL_PARTS = ("database",) + FOLDER_PARTS


@dataclass
class Paths:
    database: Path
    frames: Path
    previews: Path
    embeddings: Path
    temp: Path

    def part(self, name: str) -> Path:
        return getattr(self, name)


# ------------------------------------------------------------ layout

def shard(video_id: int) -> str:
    # 1,000 clips per sub-folder keeps Explorer and NTFS happy
    return f"{video_id // 1000:04d}"


def frame_dir(paths: Paths, video_id: int) -> Path:
    return paths.frames / shard(video_id) / str(video_id)


def frame_path(paths: Paths, video_id: int, idx: int) -> Path:
    return frame_dir(paths, video_id) / f"{idx:03d}.jpg"


def preview_path(paths: Paths, video_id: int) -> Path:
    return paths.previews / shard(video_id) / f"{video_id}.mp4"


def embeddings_file(paths: Paths, model_key: str) -> Path:
    return paths.embeddings / f"{model_key}.db"


def lock_path(paths: Paths) -> Path:
    return paths.database.parent / "indexing.lock"


# ------------------------------------------------------------ id files

def write_id_file(folder: Path, cache_id: str, kind: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ID_FILENAME).write_text(
        json.dumps({"app": "TokTidy", "cache_id": cache_id, "kind": kind}),
        encoding="utf-8",
    )


def read_id_file(folder: Path) -> dict | None:
    try:
        return json.loads((folder / ID_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_db_cache_id(db_file: Path) -> str | None:
    try:
        conn = sqlite3.connect(f"{Path(db_file).resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='cache_id'").fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


# ------------------------------------------------------------ init / verify

def init_cache(root: Path, overrides: dict | None = None, force: bool = False) -> Paths:
    settings = load_settings()
    existing = settings["paths"].get("database")
    if existing and Path(existing).exists() and not force:
        raise ConfigError(
            f"A library already exists at {existing}.\n"
            "Use `toktidy relink-cache` / `toktidy move-cache` to change locations, "
            "or add --force to start a brand-new, empty library."
        )
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    root = Path(root).resolve()
    db_dir = Path(overrides.get("database_dir", root)).resolve()
    locations = {
        "database": db_dir / "library.db",
        "frames": Path(overrides.get("frames", root / "frames")).resolve(),
        "previews": Path(overrides.get("previews", root / "previews")).resolve(),
        "embeddings": Path(overrides.get("embeddings", root / "embeddings")).resolve(),
    }
    db_dir.mkdir(parents=True, exist_ok=True)
    if locations["database"].exists() and not force:
        raise ConfigError(
            f"{locations['database']} already exists. To reuse it, run:\n"
            f'  toktidy relink-cache database "{locations["database"]}"'
        )
    if locations["database"].exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(locations["database"]) + suffix)
            if p.exists():
                p.unlink()

    cache_id = uuid.uuid4().hex
    conn = db.connect(locations["database"])
    db.create_schema(conn)
    db.set_meta(conn, "cache_id", cache_id)
    conn.commit()
    conn.close()
    for part in FOLDER_PARTS:
        write_id_file(locations[part], cache_id, part)

    for k, v in locations.items():
        settings["paths"][k] = str(v)
    save_settings(settings)
    return get_paths(settings)


def _temp_dir(settings) -> Path:
    t = settings["paths"].get("temp")
    p = Path(t) if t else Path(tempfile.gettempdir()) / "TokTidy"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_paths(settings: dict | None = None) -> Paths:
    """Return verified cache paths, or raise ConfigError with instructions."""
    settings = settings or load_settings()
    sp = settings["paths"]
    if not sp.get("database"):
        raise ConfigError(
            "No library set up yet. Create one with:\n"
            '  toktidy init --cache "D:\\TokTidyCache"'
        )
    db_file = Path(sp["database"])
    if not db_file.exists():
        raise ConfigError(
            f"The database was not found at:\n  {db_file}\n"
            "If you moved it, point TokTidy to the new location:\n"
            '  toktidy relink-cache database "X:\\new\\place\\library.db"'
        )
    cache_id = read_db_cache_id(db_file)
    if not cache_id:
        raise ConfigError(f"{db_file} does not look like a TokTidy database.")

    paths = {}
    for part in FOLDER_PARTS:
        folder = Path(sp[part]) if sp.get(part) else None
        info = read_id_file(folder) if folder else None
        if folder is None or not folder.is_dir() or info is None:
            raise ConfigError(
                f"The {part} folder was not found at:\n  {folder}\n"
                "If you moved it, point TokTidy to the new location:\n"
                f'  toktidy relink-cache {part} "X:\\new\\place\\{part}"'
            )
        if info.get("cache_id") != cache_id or info.get("kind") != part:
            raise ConfigError(
                f"The folder {folder} belongs to a different library or is not a {part} folder."
            )
        paths[part] = folder
    return Paths(database=db_file, temp=_temp_dir(settings), **paths)


# ------------------------------------------------------------ relink / move

def _indexing_running(paths: Paths) -> bool:
    lp = lock_path(paths)
    if not lp.exists():
        return False
    try:
        import psutil
        pid = int(lp.read_text().strip())
        return psutil.pid_exists(pid)
    except Exception:
        return False


def relink_cache(part: str, new_location: Path) -> Path:
    if part not in ALL_PARTS:
        raise ConfigError(f"Unknown cache part '{part}'. Use one of: {', '.join(ALL_PARTS)}")
    settings = load_settings()
    new_location = Path(new_location).resolve()

    if part == "database":
        if new_location.is_dir():
            new_location = new_location / "library.db"
        if not new_location.exists():
            raise ConfigError(f"No database found at {new_location}")
        new_id = read_db_cache_id(new_location)
        if not new_id:
            raise ConfigError(f"{new_location} is not a TokTidy database.")
        # Warn if the other folders disagree (they may need relinking too)
        for p in FOLDER_PARTS:
            f = settings["paths"].get(p)
            info = read_id_file(Path(f)) if f else None
            if info and info.get("cache_id") != new_id:
                print(f"Note: the current {p} folder belongs to a different library; relink it as well.")
    else:
        info = read_id_file(new_location)
        if not info:
            raise ConfigError(f"{new_location} is not a TokTidy {part} folder (no id file inside).")
        if info.get("kind") != part:
            raise ConfigError(f"{new_location} is a {info.get('kind')} folder, not a {part} folder.")
        db_file = settings["paths"].get("database")
        db_id = read_db_cache_id(Path(db_file)) if db_file else None
        if db_id and info.get("cache_id") != db_id:
            raise ConfigError(f"{new_location} belongs to a different library.")

    settings["paths"][part] = str(new_location)
    save_settings(settings)
    return new_location


def _tree_stats(folder: Path) -> tuple[int, int]:
    count = total = 0
    for dirpath, _, files in os.walk(folder):
        for f in files:
            count += 1
            total += os.path.getsize(os.path.join(dirpath, f))
    return count, total


def move_cache(part: str, destination: Path, progress=print) -> Path:
    if part not in ALL_PARTS:
        raise ConfigError(f"Unknown cache part '{part}'. Use one of: {', '.join(ALL_PARTS)}")
    settings = load_settings()
    paths = get_paths(settings)
    if _indexing_running(paths):
        raise ConfigError("Indexing is running. Stop it before moving cache folders.")
    destination = Path(destination).resolve()

    if part == "database":
        if destination.suffix.lower() != ".db":
            destination = destination / "library.db"
        if destination.exists():
            raise ConfigError(f"{destination} already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        conn = db.connect(paths.database)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        progress(f"Copying database to {destination} ...")
        shutil.copy2(paths.database, destination)
        if read_db_cache_id(destination) != read_db_cache_id(paths.database):
            destination.unlink(missing_ok=True)
            raise ConfigError("Copy verification failed; nothing was changed.")
        settings["paths"]["database"] = str(destination)
        save_settings(settings)
        for suffix in ("", "-wal", "-shm"):
            Path(str(paths.database) + suffix).unlink(missing_ok=True)
        return destination

    source = paths.part(part)
    if destination.exists() and any(destination.iterdir()):
        destination = destination / part
    if destination.exists() and any(destination.iterdir()):
        raise ConfigError(f"{destination} already exists and is not empty.")
    progress(f"Counting files in {source} ...")
    src_count, src_size = _tree_stats(source)
    progress(f"Copying {src_count:,} files ({src_size / 1e9:.2f} GB) to {destination} ...")
    shutil.copytree(source, destination, dirs_exist_ok=True)
    dst_count, dst_size = _tree_stats(destination)
    if (dst_count, dst_size) != (src_count, src_size):
        raise ConfigError(
            f"Copy verification failed ({dst_count} files / {dst_size} bytes copied, "
            f"expected {src_count} / {src_size}). The original was left untouched."
        )
    settings["paths"][part] = str(destination)
    save_settings(settings)
    progress("Copy verified. Removing the old folder ...")
    shutil.rmtree(source, ignore_errors=True)
    return destination


# ------------------------------------------------------------ per-clip cleanup

def remove_video_cache(conn, paths: Paths, video_id: int, embed_stores: dict | None = None) -> None:
    """Delete everything generated for one clip (files, embeddings, transcript)."""
    shutil.rmtree(frame_dir(paths, video_id), ignore_errors=True)
    preview_path(paths, video_id).unlink(missing_ok=True)
    from .embstore import EmbeddingStore
    stores = embed_stores
    opened = []
    if stores is None:
        stores = {}
        for f in paths.embeddings.glob("*.db"):
            s = EmbeddingStore(f)
            stores[f.stem] = s
            opened.append(s)
    for s in stores.values():
        s.delete(video_id)
        s.commit()
    for s in opened:
        s.close()
    conn.execute("DELETE FROM transcripts WHERE video_id=?", (video_id,))
    if db.has_fts(conn):
        conn.execute("DELETE FROM transcripts_fts WHERE rowid=?", (video_id,))
    db.clear_stages(conn, video_id)


def dir_size(folder: Path) -> tuple[int, int]:
    return _tree_stats(folder) if folder.exists() else (0, 0)
