"""Searching the library.

Visual search compares the query against every saved frame of every clip and
scores each clip by its best-matching frame (so you also learn *when* in the
clip the match happens). Find Similar compares whole clips.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import cache, db
from .config import ConfigError
from .embstore import EmbeddingStore
from .models import ModelManager, model_fingerprint


@dataclass
class Hit:
    video_id: int
    score: float
    time: float | None = None       # seconds into the clip where the best match is
    frame_idx: int | None = None
    snippet: str | None = None      # audio search
    row: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return Path(self.row["folder_path"]) / self.row["filename"]


# ---------------------------------------------------------------- filters

def allowed_video_ids(conn, folder_ids=None, hide_used=False) -> np.ndarray:
    q = ("SELECT v.id FROM videos v JOIN folders f ON f.id=v.folder_id "
         "WHERE v.present=1")
    args: list = []
    if folder_ids:
        q += f" AND v.folder_id IN ({','.join('?' * len(folder_ids))})"
        args += list(folder_ids)
    if hide_used:
        q += " AND v.used=0"
    return np.array([r[0] for r in conn.execute(q, args)], dtype=np.int64)


def attach_rows(conn, hits: list[Hit]) -> list[Hit]:
    if not hits:
        return hits
    ids = [h.video_id for h in hits]
    rows = {}
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        for r in conn.execute(
            "SELECT v.*, f.path AS folder_path, f.name AS folder_name FROM videos v "
            f"JOIN folders f ON f.id=v.folder_id WHERE v.id IN ({','.join('?' * len(part))})",
            part,
        ):
            rows[r["id"]] = dict(r)
    out = []
    for h in hits:
        if h.video_id in rows:
            h.row = rows[h.video_id]
            if h.frame_idx is not None and h.row.get("frame_times"):
                times = json.loads(h.row["frame_times"])
                if h.frame_idx < len(times):
                    h.time = times[h.frame_idx]
            out.append(h)
    return out


# ---------------------------------------------------------------- visual index

def store_signature(store: EmbeddingStore):
    return tuple(store.conn.execute("SELECT COUNT(*), MAX(video_id) FROM emb").fetchone())


class VisualIndex:
    """All frame vectors for one model, held in RAM."""

    def __init__(self, paths: cache.Paths, model_key: str, settings: dict):
        cfg = settings["models"].get(model_key)
        if not cfg:
            raise ConfigError(f"Unknown model '{model_key}'.")
        f = cache.embeddings_file(paths, model_key)
        if not f.exists():
            raise ConfigError(f"No {cfg.get('label', model_key)} embeddings yet. Run `toktidy index` first.")
        store = EmbeddingStore(f, model_fingerprint(model_key, cfg))
        self.signature = store_signature(store)
        self.video_ids, self.counts, self.matrix = store.load_all()
        store.close()
        import time as _t
        self.loaded_at = _t.time()
        self.offsets = np.concatenate([[0], np.cumsum(self.counts)[:-1]]).astype(np.int64)
        self.pos = {int(v): i for i, v in enumerate(self.video_ids)}
        self._means = None

    def __len__(self):
        return len(self.video_ids)

    def frame_scores(self, q: np.ndarray, chunk: int = 250_000) -> np.ndarray:
        q = q.astype(np.float32)
        out = np.empty(len(self.matrix), np.float32)
        for s in range(0, len(self.matrix), chunk):
            out[s:s + chunk] = self.matrix[s:s + chunk].astype(np.float32) @ q
        return out

    def clip_scores(self, q: np.ndarray):
        """(best score per clip, frame scores)."""
        fs = self.frame_scores(q)
        if len(fs) == 0:
            return np.zeros(0, np.float32), fs
        return np.maximum.reduceat(fs, self.offsets), fs

    def best_frame(self, pos: int, frame_scores: np.ndarray) -> int:
        o, n = self.offsets[pos], self.counts[pos]
        return int(np.argmax(frame_scores[o:o + n]))

    def means(self) -> np.ndarray:
        if self._means is None:
            sums = np.add.reduceat(self.matrix.astype(np.float32), self.offsets, axis=0)
            norms = np.linalg.norm(sums, axis=1, keepdims=True)
            self._means = sums / np.maximum(norms, 1e-6)
        return self._means

    def clip_vectors(self, video_id: int) -> np.ndarray:
        p = self.pos.get(int(video_id))
        if p is None:
            raise LookupError(f"Clip {video_id} has no embeddings for this model yet.")
        o, n = self.offsets[p], self.counts[p]
        return self.matrix[o:o + n].astype(np.float32)


def _rank(index: VisualIndex, scores: np.ndarray, allowed: np.ndarray, limit: int,
          frame_scores: np.ndarray | None, exclude=()) -> list[Hit]:
    mask = np.isin(index.video_ids, allowed)
    if exclude:
        mask &= ~np.isin(index.video_ids, np.array(list(exclude), np.int64))
    cand = np.nonzero(mask)[0]
    if cand.size == 0:
        return []
    s = scores[cand]
    k = min(limit, cand.size) if limit else cand.size
    top = cand[np.argpartition(-s, k - 1)[:k]] if k < cand.size else cand
    top = top[np.argsort(-scores[top])]
    hits = []
    for p in top:
        fi = index.best_frame(p, frame_scores) if frame_scores is not None else None
        hits.append(Hit(video_id=int(index.video_ids[p]), score=float(scores[p]), frame_idx=fi))
    return hits


def parse_query(query: str) -> list[str]:
    """'red dress + beach' -> ['red dress', 'beach']"""
    return [t.strip() for t in re.split(r"\s*\+\s*", query) if t.strip()]


class Searcher:
    """Keeps search data in RAM between searches and refreshes it as indexing adds clips."""

    NEGATIVE_WEIGHT = 0.5
    REFRESH_SECONDS = 90   # while indexing, reload new search data at most this often

    def __init__(self, conn, paths, settings, log=print, models: ModelManager | None = None):
        import threading
        self.conn, self.paths, self.settings, self.log = conn, paths, settings, log
        self.models = models or ModelManager(settings, log)
        self.indexes: dict[str, VisualIndex] = {}
        self._index_lock = threading.Lock()
        self._text_cache: dict = {}

    def index(self, key, force=False) -> VisualIndex:
        import time as _t
        with self._index_lock:
            idx = self.indexes.get(key)
            if idx is not None and not force:
                if _t.time() - idx.loaded_at < self.REFRESH_SECONDS:
                    return idx
                f = cache.embeddings_file(self.paths, key)
                st = EmbeddingStore(f)
                sig = store_signature(st)
                st.close()
                if sig == idx.signature:
                    idx.loaded_at = _t.time()
                    return idx
                self.log(f"Refreshing {key} search data ...")
            self.indexes[key] = VisualIndex(self.paths, key, self.settings)
            return self.indexes[key]

    # ------------------------------------------------------------ encoding
    def _phrasings(self):
        sc = self.settings.get("search") or {}
        return list(sc.get("phrasings") or []) if sc.get("multi_phrasing") else []

    def _embedder(self, key, group=()):
        return self.models.embedder(key, protect=[k for k in group if k != key])

    def encode_texts(self, key, texts, group=()):
        tpls = self._phrasings()
        tag = tuple(tpls)
        missing = [t for t in texts if (key, tag, t) not in self._text_cache]
        if missing:
            with self.models.lock:
                vecs = self._embedder(key, group).encode_queries(missing, tpls)
            for t, v in zip(missing, vecs):
                if len(self._text_cache) > 500:
                    self._text_cache.clear()
                self._text_cache[(key, tag, t)] = v
        return np.stack([self._text_cache[(key, tag, t)] for t in texts]) if texts else np.zeros((0, 1))

    def encode_image(self, key, image: Image.Image, group=()):
        with self.models.lock:
            return self._embedder(key, group).encode_images([image.convert("RGB")])

    def _combine(self, index, vectors_pos, vectors_neg):
        total, first_fs = None, None
        for v in vectors_pos:
            s, fs = index.clip_scores(v)
            total = s if total is None else total + s
            first_fs = fs if first_fs is None else first_fs
        total = total / max(1, len(vectors_pos))
        for v in vectors_neg:
            s, _ = index.clip_scores(v)
            total = total - self.NEGATIVE_WEIGHT * np.maximum(s, 0)
        return total, first_fs

    # ------------------------------------------------------------ scoring per model
    def _text_part(self, key, terms, negatives, group):
        idx = self.index(key)
        pos = self.encode_texts(key, terms, group)
        neg = self.encode_texts(key, list(negatives), group) if negatives else []
        scores, fs = self._combine(idx, list(pos), list(neg))
        return idx, scores, fs

    def _image_part(self, key, img, negatives, group):
        idx = self.index(key)
        q = self.encode_image(key, img, group)
        neg = self.encode_texts(key, list(negatives), group) if negatives else []
        scores, fs = self._combine(idx, list(q), list(neg))
        return idx, scores, fs

    def _similar_part(self, key, video_ids):
        idx = self.index(key)
        vecs = []
        for v in video_ids:
            try:
                vecs.append(idx.clip_vectors(v).mean(0))
            except LookupError:
                pass
        if not vecs:
            raise LookupError(f"The chosen clip(s) have no {key} search data yet.")
        q = np.mean(vecs, axis=0)
        q /= max(np.linalg.norm(q), 1e-6)
        return idx, idx.means() @ q, None

    def _finish(self, parts, folder_ids, limit, hide_used, raw, exclude=()):
        allowed = allowed_video_ids(self.conn, folder_ids, hide_used)
        if len(parts) == 1:
            idx, scores, fs = parts[0]
            hits = _rank(idx, scores, allowed, limit, fs, exclude=exclude)
        else:
            hits = _fuse(parts, allowed, limit, exclude=exclude)
        return hits if raw else attach_rows(self.conn, hits)

    # ------------------------------------------------------------ public
    # `model_key` may be one key or a list of keys ("Both models").
    def text(self, model_key, query, folder_ids=None, negatives=(), limit=50, hide_used=False, raw=False):
        terms = parse_query(query)
        if not terms:
            raise ConfigError("Type something to search for.")
        keys = _keys(model_key)
        parts = [self._text_part(k, terms, negatives, keys) for k in keys]
        return self._finish(parts, folder_ids, limit, hide_used, raw)

    def image(self, model_key, image, folder_ids=None, negatives=(), limit=50, hide_used=False, raw=False):
        """`image` is a file path or a PIL image."""
        keys = _keys(model_key)
        if isinstance(image, Image.Image):
            img = image
        else:
            with Image.open(image) as im:
                img = im.convert("RGB")
        parts = [self._image_part(k, img, negatives, keys) for k in keys]
        return self._finish(parts, folder_ids, limit, hide_used, raw)

    def similar(self, model_key, video_ids, folder_ids=None, limit=50, hide_used=False, raw=False):
        keys = _keys(model_key)
        parts, err = [], None
        for k in keys:
            try:
                parts.append(self._similar_part(k, video_ids))
            except LookupError as e:
                err = e
        if not parts:
            raise err
        return self._finish(parts, folder_ids, limit, hide_used, raw, exclude=set(video_ids))


def _keys(model_key):
    return [model_key] if isinstance(model_key, str) else list(model_key)


RRF_K = 60


def _fuse(parts, allowed, limit, exclude=()) -> list[Hit]:
    """Merge rankings from several models (reciprocal rank fusion).

    The models score on different scales, so ranks are combined rather than
    raw scores: clips that every model places high end up on top.
    """
    all_ids = np.unique(np.concatenate([p[0].video_ids for p in parts])).astype(np.int64)
    fused = np.zeros(len(all_ids), np.float64)
    best_rank = np.full(len(all_ids), np.iinfo(np.int64).max, np.int64)
    best_part = np.full(len(all_ids), -1, np.int64)
    excl = np.array(list(exclude), np.int64)
    for pi, (idx, scores, _fs) in enumerate(parts):
        ids = np.asarray(idx.video_ids, np.int64)
        mask = np.isin(ids, allowed)
        if excl.size:
            mask &= ~np.isin(ids, excl)
        cand = np.nonzero(mask)[0]
        if cand.size == 0:
            continue
        order = cand[np.argsort(-scores[cand], kind="stable")]
        ranks = np.arange(order.size, dtype=np.int64)
        g = np.searchsorted(all_ids, ids[order])
        fused[g] += 1.0 / (RRF_K + 1 + ranks)
        better = ranks < best_rank[g]
        best_rank[g[better]] = ranks[better]
        best_part[g[better]] = pi
    cand = np.nonzero(best_part >= 0)[0]
    if cand.size == 0:
        return []
    k = min(limit, cand.size) if limit else cand.size
    top = cand[np.argpartition(-fused[cand], k - 1)[:k]] if k < cand.size else cand
    top = top[np.lexsort((best_rank[top], -fused[top]))]
    hits = []
    for g in top:
        vid = int(all_ids[g])
        idx, _scores, fs = parts[best_part[g]]
        fi = idx.best_frame(idx.pos[vid], fs) if fs is not None else None
        hits.append(Hit(video_id=vid, score=float(fused[g]), frame_idx=fi))
    return hits


# ---------------------------------------------------------------- color

def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def parse_color(text: str) -> tuple[int, int, int]:
    t = text.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", t):
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
    try:
        from PIL import ImageColor
        return ImageColor.getrgb(text.strip())[:3]
    except ValueError:
        raise ConfigError(f"'{text}' is not a colour. Use a name like 'red' or a hex code like #ff3366.")


def color_search(conn, rgb, folder_ids=None, limit=50, hide_used=False) -> list[Hit]:
    """Rank clips by how much of their palette is close to `rgb`."""
    target = _srgb_to_lab(np.array(rgb, np.float32))
    q = ("SELECT v.id, v.colors FROM videos v WHERE v.present=1 AND v.colors IS NOT NULL")
    args: list = []
    if folder_ids:
        q += f" AND v.folder_id IN ({','.join('?' * len(folder_ids))})"
        args += list(folder_ids)
    if hide_used:
        q += " AND v.used=0"
    hits = []
    for vid, colors in conn.execute(q, args):
        pal = json.loads(colors)
        if not pal:
            continue
        arr = np.array(pal, np.float32)
        dist = np.linalg.norm(_srgb_to_lab(arr[:, :3]) - target, axis=1)
        score = float((arr[:, 3] * np.exp(-dist / 20.0)).sum())
        hits.append(Hit(video_id=vid, score=score))
    hits.sort(key=lambda h: -h.score)
    return attach_rows(conn, hits[:limit] if limit else hits)


# ---------------------------------------------------------------- audio

def _segment_time(segments_json, words):
    try:
        segs = json.loads(segments_json or "[]")
    except json.JSONDecodeError:
        return None, None
    wl = [w.lower() for w in words]
    for start, end, text in segs:
        tl = text.lower()
        if all(w in tl for w in wl):
            return start, text
    for start, end, text in segs:
        if any(w in text.lower() for w in wl):
            return start, text
    if segs:
        try:
            from rapidfuzz import fuzz
            q = " ".join(wl)
            best = max(segs, key=lambda s: fuzz.partial_ratio(q, s[2].lower()))
            return best[0], best[2]
        except ImportError:
            pass
    return None, None


def audio_search(conn, query: str, folder_ids=None, limit=50, hide_used=False, fuzzy=True) -> list[Hit]:
    query = query.strip()
    if not query:
        raise ConfigError("Type a word or phrase to search for.")
    words = re.findall(r"\w+", query.lower())
    allowed = set(allowed_video_ids(conn, folder_ids, hide_used).tolist())
    hits: dict[int, Hit] = {}

    if db.has_fts(conn) and words:
        phrase = '"' + " ".join(words) + '"'
        for vid, rank in conn.execute(
            "SELECT rowid, bm25(transcripts_fts) FROM transcripts_fts WHERE transcripts_fts MATCH ? "
            "ORDER BY bm25(transcripts_fts) LIMIT 5000", (phrase,)
        ):
            if vid in allowed:
                hits[vid] = Hit(video_id=vid, score=1000 - float(rank))
    else:
        like = f"%{query.lower()}%"
        for vid, in conn.execute("SELECT video_id FROM transcripts WHERE lower(text) LIKE ?", (like,)):
            if vid in allowed:
                hits[vid] = Hit(video_id=vid, score=1000.0)

    if fuzzy and len(query) >= 4 and len(hits) < (limit or 50):
        try:
            from rapidfuzz import fuzz, process
            texts = {vid: t for vid, t in conn.execute(
                "SELECT video_id, lower(text) FROM transcripts WHERE text<>''") if vid in allowed}
            for _, score, vid in process.extract(
                query.lower(), texts, scorer=fuzz.partial_ratio, score_cutoff=78,
                limit=(limit or 50) * 2,
            ):
                if vid not in hits:
                    hits[vid] = Hit(video_id=vid, score=float(score))
        except ImportError:
            pass

    ordered = sorted(hits.values(), key=lambda h: -h.score)
    if limit:
        ordered = ordered[:limit]
    out = attach_rows(conn, ordered)
    segs = {vid: s for vid, s in conn.execute(
        f"SELECT video_id, segments FROM transcripts WHERE video_id IN ({','.join('?' * len(out))})",
        [h.video_id for h in out],
    )} if out else {}
    for h in out:
        h.time, h.snippet = _segment_time(segs.get(h.video_id), words)
    return out


# ---------------------------------------------------------------- browsing

SORTS = {
    "name": "v.filename COLLATE NOCASE",
    "folder": "f.name COLLATE NOCASE",
    "modified": "v.mtime",
    "added": "v.added_at",
    "duration": "v.duration",
    "size": "v.size",
    "resolution": "(v.width * v.height)",
    "brightness": "v.brightness",
    "motion": "v.motion",
    "pace": "v.pace",
}


def browse_ids(conn, folder_ids=None, hide_used=False, sort="name", descending=False,
               seed=None) -> list[int]:
    """All clips in the chosen folders, in the chosen order ('shuffle' uses `seed`)."""
    q = "SELECT v.id FROM videos v JOIN folders f ON f.id=v.folder_id WHERE v.present=1"
    args: list = []
    if folder_ids:
        q += f" AND v.folder_id IN ({','.join('?' * len(folder_ids))})"
        args += list(folder_ids)
    if hide_used:
        q += " AND v.used=0"
    if sort != "shuffle":
        col = SORTS.get(sort, SORTS["name"])
        d = "DESC" if descending else "ASC"
        # clips without a value (not indexed yet) always go last
        q += f" ORDER BY ({col}) IS NULL, {col} {d}, v.filename COLLATE NOCASE"
    ids = [r[0] for r in conn.execute(q, args)]
    if sort == "shuffle":
        import random
        random.Random(seed).shuffle(ids)
    return ids
