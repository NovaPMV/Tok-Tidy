"""Folder scanning.

Compares a folder on disk with what the library remembers:
  * unchanged clips (same name, size, modified time) are left alone
  * edited clips are reset so they get re-indexed
  * new clips are added, unless they match a clip that disappeared from
    somewhere else (renamed or moved) - then the existing index is reused
  * clips that vanished are marked missing (kept, so a later move can reclaim them)
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import cache, db


def quick_hash(path: Path, size: int, chunk: int = 65536) -> str:
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(size - chunk)
            h.update(f.read(chunk))
    return h.hexdigest()


def list_video_files(folder: Path, extensions) -> dict[str, tuple[int, float]]:
    exts = {e.lower() for e in extensions}
    out = {}
    with os.scandir(folder) as it:
        for entry in it:
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            if os.path.splitext(entry.name)[1].lower() not in exts:
                continue
            st = entry.stat()
            out[entry.name] = (st.st_size, st.st_mtime)
    return out


@dataclass
class ScanResult:
    folder: str
    total: int = 0
    new: int = 0
    changed: int = 0
    missing: int = 0
    returned: int = 0
    renamed: int = 0
    moved_in: int = 0
    offline: bool = False

    def summary(self) -> str:
        if self.offline:
            return f"{self.folder}: OFFLINE (folder not found; nothing changed)"
        parts = [f"{self.total:,} clips"]
        for label, n in (("new", self.new), ("changed", self.changed), ("renamed", self.renamed),
                         ("moved in", self.moved_in), ("missing", self.missing),
                         ("back", self.returned)):
            if n:
                parts.append(f"{n:,} {label}")
        return f"{self.folder}: " + ", ".join(parts)


RESET_COLUMNS = ("duration", "width", "height", "fps", "vcodec", "has_audio",
                 "frame_times", "brightness", "motion", "pace", "colors")


def scan_folder(conn, paths: cache.Paths, settings: dict, folder) -> ScanResult:
    fpath = Path(folder["path"])
    result = ScanResult(folder=folder["name"])
    if not fpath.is_dir():
        conn.execute("UPDATE folders SET online=0 WHERE id=?", (folder["id"],))
        conn.commit()
        result.offline = True
        return result

    disk = list_video_files(fpath, settings["video_extensions"])
    result.total = len(disk)
    rows = conn.execute(
        "SELECT id, filename, size, mtime, present FROM videos WHERE folder_id=?", (folder["id"],)
    ).fetchall()
    by_name = {r["filename"].lower(): r for r in rows}
    seen: set[int] = set()
    new_names = []
    now = time.time()

    for name, (size, mtime) in disk.items():
        r = by_name.get(name.lower())
        if r is None:
            new_names.append(name)
            continue
        seen.add(r["id"])
        if r["size"] == size and abs(r["mtime"] - mtime) < 2:
            if not r["present"]:
                conn.execute("UPDATE videos SET present=1 WHERE id=?", (r["id"],))
                result.returned += 1
            continue
        # contents changed -> forget old index for this clip
        cache.remove_video_cache(conn, paths, r["id"])
        conn.execute(
            f"UPDATE videos SET size=?, mtime=?, qhash=?, present=1, filename=?, "
            + ", ".join(f"{c}=NULL" for c in RESET_COLUMNS)
            + " WHERE id=?",
            (size, mtime, quick_hash(fpath / name, size), name, r["id"]),
        )
        result.changed += 1

    for r in rows:
        if r["id"] not in seen and r["present"]:
            conn.execute("UPDATE videos SET present=0 WHERE id=?", (r["id"],))
            result.missing += 1

    for name in sorted(new_names):
        size, mtime = disk[name]
        ext = os.path.splitext(name)[1].lower()
        # Only hash when an existing clip of the same size might have been moved/renamed here.
        match = None
        qh = None
        candidates = [
            c for c in conn.execute(
                "SELECT v.id, v.filename, v.folder_id, v.qhash, f.path FROM videos v "
                "JOIN folders f ON f.id=v.folder_id WHERE v.size=? AND v.qhash IS NOT NULL",
                (size,),
            )
            if c["id"] not in seen
            and (c["folder_id"] == folder["id"] or not (Path(c["path"]) / c["filename"]).exists())
        ]
        if candidates:
            try:
                qh = quick_hash(fpath / name, size)
            except OSError:
                continue
            match = next((c for c in candidates if c["qhash"] == qh), None)
        if match:
            conn.execute(
                "UPDATE videos SET folder_id=?, filename=?, ext=?, mtime=?, present=1 WHERE id=?",
                (folder["id"], name, ext, mtime, match["id"]),
            )
            seen.add(match["id"])
            if match["folder_id"] == folder["id"]:
                result.renamed += 1
                result.missing -= 1 if result.missing > 0 else 0
            else:
                result.moved_in += 1
        else:
            conn.execute(
                "INSERT INTO videos(folder_id, filename, ext, size, mtime, qhash, added_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (folder["id"], name, ext, size, mtime, qh, now),
            )
            result.new += 1

    conn.execute("UPDATE folders SET last_scan_at=?, online=1 WHERE id=?", (now, folder["id"]))
    conn.commit()
    return result


def relink_folder(conn, paths, settings, folder, new_path: Path) -> ScanResult:
    new_path = Path(new_path).resolve()
    if not new_path.is_dir():
        raise LookupError(f"{new_path} is not a folder.")
    clash = conn.execute(
        "SELECT id FROM folders WHERE path=? AND id<>?", (str(new_path), folder["id"])
    ).fetchone()
    if clash:
        raise LookupError(f"{new_path} is already in the library as folder id {clash['id']}.")
    conn.execute("UPDATE folders SET path=?, name=? WHERE id=?",
                 (str(new_path), new_path.name, folder["id"]))
    conn.commit()
    folder = conn.execute("SELECT * FROM folders WHERE id=?", (folder["id"],)).fetchone()
    return scan_folder(conn, paths, settings, folder)


def purge_missing(conn, paths, folder_id: int | None = None) -> int:
    q = "SELECT id FROM videos WHERE present=0"
    args = ()
    if folder_id is not None:
        q += " AND folder_id=?"
        args = (folder_id,)
    ids = [r["id"] for r in conn.execute(q, args)]
    for vid in ids:
        cache.remove_video_cache(conn, paths, vid)
        conn.execute("DELETE FROM videos WHERE id=?", (vid,))
    conn.commit()
    return len(ids)
