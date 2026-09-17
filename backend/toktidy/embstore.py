"""Per-model embedding files (embeddings\\<model>.db).

Each clip stores one row: an (n_frames x dim) float16 matrix, in the same
order as the clip's saved frames. Each file remembers which exact model made
it, so embeddings from different models are never mixed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from .config import ConfigError


class EmbeddingStore:
    def __init__(self, path: Path, fingerprint: str | None = None):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path), timeout=60)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS emb (
                video_id INTEGER PRIMARY KEY,
                n        INTEGER NOT NULL,
                dim      INTEGER NOT NULL,
                data     BLOB NOT NULL
            );
            """
        )
        stored = self.get_meta("fingerprint")
        if fingerprint:
            if stored is None:
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('fingerprint', ?)", (fingerprint,)
                )
                self.conn.commit()
            elif stored != fingerprint:
                raise ConfigError(
                    f"{self.path.name} was created with a different model:\n"
                    f"  stored:   {stored}\n  settings: {fingerprint}\n"
                    f"Either restore the old model in settings, or clear these embeddings with:\n"
                    f"  toktidy reset-model {self.path.stem}"
                )

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, video_id: int, vectors: np.ndarray) -> None:
        v = np.ascontiguousarray(vectors, dtype=np.float16)
        self.conn.execute(
            "INSERT OR REPLACE INTO emb(video_id, n, dim, data) VALUES(?,?,?,?)",
            (int(video_id), int(v.shape[0]), int(v.shape[1]), v.tobytes()),
        )

    def get(self, video_id: int) -> np.ndarray | None:
        row = self.conn.execute(
            "SELECT n, dim, data FROM emb WHERE video_id=?", (int(video_id),)
        ).fetchone()
        if not row:
            return None
        n, dim, data = row
        return np.frombuffer(data, dtype=np.float16).reshape(n, dim).astype(np.float32)

    def delete(self, video_id: int) -> None:
        self.conn.execute("DELETE FROM emb WHERE video_id=?", (int(video_id),))

    def video_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT video_id FROM emb")}

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM emb").fetchone()[0]

    def load_all(self):
        """Returns (video_ids int64[V], counts int64[V], matrix float16[N, dim])."""
        ids, counts, chunks = [], [], []
        dim = None
        for vid, n, d, data in self.conn.execute(
            "SELECT video_id, n, dim, data FROM emb ORDER BY video_id"
        ):
            if dim is None:
                dim = d
            if d != dim or n == 0:
                continue
            ids.append(vid)
            counts.append(n)
            chunks.append(np.frombuffer(data, dtype=np.float16).reshape(n, d))
        if not ids:
            return (np.zeros(0, np.int64), np.zeros(0, np.int64),
                    np.zeros((0, dim or 1), np.float16))
        return np.array(ids, np.int64), np.array(counts, np.int64), np.concatenate(chunks)

    def clear(self) -> None:
        self.conn.execute("DELETE FROM emb")
        self.conn.execute("DELETE FROM meta WHERE key='fingerprint'")
        self.conn.commit()
        self.conn.execute("VACUUM")

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()
