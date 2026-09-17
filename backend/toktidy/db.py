"""The library database (library.db).

Holds the remembered folders, every clip's details, per-stage indexing status,
used markers, colour/motion scores and transcripts. Embeddings are kept in
separate files (see embstore.py) because they are much larger.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 1

# Indexing stages. A clip with no row for a stage is "pending".
META = "meta"
FRAMES = "frames"
PREVIEW = "preview"
ANALYSIS = "analysis"
TRANSCRIBE = "transcribe"
DECODE_STAGES = (META, FRAMES, PREVIEW, ANALYSIS)


def embed_stage(model_key: str) -> str:
    return f"embed:{model_key}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS folders (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name         TEXT NOT NULL,
    added_at     REAL NOT NULL,
    last_scan_at REAL,
    online       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY,
    folder_id   INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL COLLATE NOCASE,
    ext         TEXT,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    qhash       TEXT,
    present     INTEGER NOT NULL DEFAULT 1,
    added_at    REAL NOT NULL,
    duration    REAL,
    width       INTEGER,
    height      INTEGER,
    fps         REAL,
    vcodec      TEXT,
    has_audio   INTEGER,
    frame_times TEXT,
    brightness  REAL,
    motion      REAL,
    pace        REAL,
    colors      TEXT,
    used        INTEGER NOT NULL DEFAULT 0,
    used_at     REAL,
    UNIQUE(folder_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_videos_folder ON videos(folder_id);
CREATE INDEX IF NOT EXISTS idx_videos_size   ON videos(size);

CREATE TABLE IF NOT EXISTS stages (
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL,
    status     TEXT NOT NULL,          -- done | failed
    error      TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (video_id, stage)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_stages_stage ON stages(stage, status);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id      INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    language      TEXT,
    language_prob REAL,
    text          TEXT NOT NULL DEFAULT '',
    segments      TEXT
);
"""


def connect(path: Path | str, shared: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=60, check_same_thread=not shared)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts "
            "USING fts5(text, tokenize='unicode61 remove_diacritics 2')"
        )
    except sqlite3.OperationalError:
        pass  # FTS5 missing: audio search falls back to slower matching
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='transcripts_fts'"
    ).fetchone()
    return row is not None


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


# ---------------------------------------------------------------- stages

def set_stage(conn, video_id: int, stage: str, status: str = "done", error: str | None = None):
    conn.execute(
        "INSERT INTO stages(video_id, stage, status, error, updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(video_id, stage) DO UPDATE SET status=excluded.status, "
        "error=excluded.error, updated_at=excluded.updated_at",
        (video_id, stage, status, error, time.time()),
    )


def clear_stages(conn, video_id: int, stages=None):
    if stages is None:
        conn.execute("DELETE FROM stages WHERE video_id=?", (video_id,))
    else:
        conn.executemany(
            "DELETE FROM stages WHERE video_id=? AND stage=?",
            [(video_id, s) for s in stages],
        )


def stage_statuses(conn, video_id: int) -> dict:
    return {
        r["stage"]: r["status"]
        for r in conn.execute("SELECT stage, status FROM stages WHERE video_id=?", (video_id,))
    }


# ---------------------------------------------------------------- folders

def add_folder(conn, path: Path) -> tuple[int, bool]:
    """Returns (folder_id, created)."""
    path = Path(path).resolve()
    row = conn.execute("SELECT id FROM folders WHERE path=?", (str(path),)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO folders(path, name, added_at) VALUES(?,?,?)",
        (str(path), path.name, time.time()),
    )
    conn.commit()
    return cur.lastrowid, True


def all_folders(conn):
    return conn.execute("SELECT * FROM folders ORDER BY name COLLATE NOCASE").fetchall()


def resolve_folder(conn, ref: str):
    """Find a folder by id, full path, or (unique) folder name."""
    ref = str(ref).strip().strip('"')
    if ref.isdigit():
        row = conn.execute("SELECT * FROM folders WHERE id=?", (int(ref),)).fetchone()
        if row:
            return row
    try:
        p = str(Path(ref).resolve())
        row = conn.execute("SELECT * FROM folders WHERE path=?", (p,)).fetchone()
        if row:
            return row
    except OSError:
        pass
    rows = conn.execute(
        "SELECT * FROM folders WHERE name=? COLLATE NOCASE", (ref,)
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        ids = ", ".join(str(r["id"]) for r in rows)
        raise LookupError(f'Several folders are named "{ref}" (ids {ids}). Use the id instead.')
    raise LookupError(f'No remembered folder matches "{ref}". Run `toktidy folders` to see the list.')


def video_path(conn, video_id: int) -> Path:
    row = conn.execute(
        "SELECT f.path, v.filename FROM videos v JOIN folders f ON f.id=v.folder_id WHERE v.id=?",
        (video_id,),
    ).fetchone()
    if not row:
        raise LookupError(f"No clip with id {video_id}")
    return Path(row["path"]) / row["filename"]


def find_video_by_path(conn, path: Path):
    path = Path(path).resolve()
    return conn.execute(
        "SELECT v.* FROM videos v JOIN folders f ON f.id=v.folder_id "
        "WHERE f.path=? AND v.filename=?",
        (str(path.parent), path.name),
    ).fetchone()
