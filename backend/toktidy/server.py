"""Local backend for the TokTidy desktop app.

The Electron app starts this process, talks to it over http://127.0.0.1,
and shuts it down on exit. Nothing is reachable from other computers.
"""
from __future__ import annotations

import base64
import collections
import io
import json
import logging
import re
import logging.handlers
import os
import secrets
import socket
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, cache, db, scanner
from .config import ConfigError, _deep_merge, app_dir, enabled_models, load_settings, resolve_models, save_settings
from .models import ModelManager
from .search import Hit, Searcher, allowed_video_ids, attach_rows, audio_search, browse_ids, color_search, parse_color

MAX_RESULTS = 20000
TOKEN = os.environ.get("TOKTIDY_TOKEN") or secrets.token_hex(16)

# ---------------------------------------------------------------- logging

LOG_DIR = app_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("toktidy")
logger.setLevel(logging.INFO)
_fh = logging.handlers.RotatingFileHandler(LOG_DIR / "backend.log", maxBytes=2_000_000, backupCount=3,
                                           encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_fh)


class ProgressBar:
    """Stands in for tqdm; feeds the app's progress display."""

    def __init__(self, job, total, desc):
        self.job, self.total = job, total
        with job.lock:
            job.status.update(step=desc, done=0, total=total, step_started=time.time(), step_failed=0)

    def update(self, n=1):
        with self.job.lock:
            self.job.status["done"] += n

    def set_postfix_str(self, s):
        try:
            with self.job.lock:
                self.job.status["step_failed"] = int(str(s).split()[0])
        except (ValueError, IndexError):
            pass

    def close(self):
        pass


class IndexJob(threading.Thread):
    def __init__(self, backend, folder_ids, only=None, models=None, limit=None):
        super().__init__(daemon=True, name="indexer")
        self.backend = backend
        self.folder_ids, self.only, self.models, self.limit = folder_ids, only, models, limit
        self.lock = threading.Lock()
        self.indexer = None
        self.status = {
            "running": True, "stopping": False, "started": time.time(), "finished": None,
            "folder_n": 0, "folder_total": 0, "folder": "", "step": "Starting ...",
            "done": 0, "total": 0, "step_started": time.time(), "step_failed": 0,
            "result": None, "error": None,
        }

    def stop(self):
        with self.lock:
            self.status["stopping"] = True
        if self.indexer:
            self.indexer.stop.set()

    def snapshot(self):
        with self.lock:
            s = dict(self.status)
        el = time.time() - s["step_started"]
        s["rate"] = s["done"] / el if el > 1 and s["done"] else None
        s["eta"] = (s["total"] - s["done"]) / s["rate"] if s["rate"] else None
        return s

    def _on_folder(self, n, total, folder):
        with self.lock:
            self.status.update(folder_n=n, folder_total=total, folder=folder["name"],
                               step="Checking for new clips ...", done=0, total=0)

    def run(self):
        b = self.backend
        conn = None
        try:
            settings = load_settings()
            conn = db.connect(b.paths.database)
            if self.folder_ids:
                folders = [conn.execute("SELECT * FROM folders WHERE id=?", (i,)).fetchone()
                           for i in self.folder_ids]
                folders = [f for f in folders if f]
            else:
                folders = db.all_folders(conn)
            from .indexer import Indexer
            self.indexer = Indexer(conn, b.paths, settings, log=b.log, models=b.models,
                                   progress=lambda total, desc: ProgressBar(self, total, desc),
                                   on_folder=self._on_folder)
            if self.status["stopping"]:
                self.indexer.stop.set()
            finished = self.indexer.run(folders, only=self.only, model_keys=self.models, limit=self.limit)
            self.status["result"] = "finished" if finished else "stopped"
        except ConfigError as e:
            self.status["error"] = str(e)
            b.log(f"Indexing error: {e}")
        except Exception as e:
            self.status["error"] = f"{type(e).__name__}: {e}"
            b.log("Indexing crashed:\n" + traceback.format_exc())
        finally:
            if conn:
                conn.close()
            with self.lock:
                self.status.update(running=False, finished=time.time())
            if b.searcher:
                for idx in b.searcher.indexes.values():
                    idx.loaded_at = 0  # re-check search data on next search


class Task(threading.Thread):
    """A generic background job (moving cache, measuring sizes, cleanup)."""

    def __init__(self, backend, name, fn):
        super().__init__(daemon=True, name=name)
        self.backend, self.fn = backend, fn
        self.status = {"name": name, "running": True, "messages": [], "result": None, "error": None}

    def say(self, msg):
        self.status["messages"].append(str(msg))
        self.backend.log(f"[{self.status['name']}] {msg}")

    def run(self):
        try:
            self.status["result"] = self.fn(self.say)
        except Exception as e:
            self.status["error"] = str(e) if isinstance(e, (ConfigError, LookupError)) else f"{type(e).__name__}: {e}"
            self.backend.log(traceback.format_exc())
        finally:
            self.status["running"] = False


class Backend:
    def __init__(self):
        self.log_lines = collections.deque(maxlen=3000)
        self.lock = threading.RLock()
        self.views: collections.OrderedDict[str, dict] = collections.OrderedDict()
        self.job: IndexJob | None = None
        self.task: Task | None = None
        self.settings = None
        self.paths = None
        self.error = None
        self.models = None
        self.searcher = None
        self.search_conn = None
        self.search_lock = threading.Lock()
        self._stats_cache = (0, None)
        self.reload()
        threading.Thread(target=self._housekeeping, daemon=True).start()

    # --------------------------------------------------------- basics
    def log(self, msg):
        for line in str(msg).rstrip().splitlines() or [""]:
            line = line.rstrip()
            self.log_lines.append(f"{time.strftime('%H:%M:%S')} {line}")
            logger.info(line)

    def reload(self):
        with self.lock:
            self.settings = load_settings()
            if self.models is None:
                self.models = ModelManager(self.settings, self.log)
            else:
                self.models.settings = self.settings
            try:
                self.paths = cache.get_paths(self.settings)
                self.error = None
                c = db.connect(self.paths.database)
                db.create_schema(c)
                c.close()
                if self.search_conn:
                    self.search_conn.close()
                self.search_conn = db.connect(self.paths.database, shared=True)
                self.searcher = Searcher(self.search_conn, self.paths, self.settings, self.log, self.models)
                threading.Thread(target=self._warm, daemon=True).start()
            except ConfigError as e:
                self.paths = None
                self.searcher = None
                self.error = str(e)
            self.views.clear()

    def _warm(self):
        try:
            keys = resolve_models(self.settings)
        except ConfigError:
            return
        for key in keys:
            try:
                if self.paths and cache.embeddings_file(self.paths, key).exists():
                    t = time.time()
                    with self.search_lock:
                        n = len(self.searcher.index(key))
                    self.log(f"Search data ready ({key}): {n:,} clips ({time.time() - t:.1f}s)")
            except Exception as e:
                self.log(f"Could not preload {key} search data: {e}")

    def _housekeeping(self):
        while True:
            time.sleep(30)
            try:
                mins = float(self.settings["app"].get("unload_models_after_min", 10))
                idle = time.time() - self.models.last_used
                busy = self.job is not None and self.job.is_alive()
                if mins > 0 and self.models.loaded and not busy and idle > mins * 60:
                    self.log("Freeing GPU memory (search idle)")
                    self.models.unload_all()
                parent = os.environ.get("TOKTIDY_PARENT_PID")
                if parent:
                    import psutil
                    if not psutil.pid_exists(int(parent)):
                        os._exit(0)
            except Exception:
                pass

    def need_library(self):
        if self.paths is None:
            raise HTTPException(409, self.error or "Library is not set up.")

    def conn(self):
        self.need_library()
        return db.connect(self.paths.database)

    def indexing(self):
        return self.job is not None and self.job.is_alive()

    def busy_task(self):
        return self.task is not None and self.task.is_alive()

    # --------------------------------------------------------- views
    def store_view(self, mode, hits, meta):
        vid = uuid.uuid4().hex[:12]
        self.views[vid] = {"mode": mode, "hits": hits, **meta}
        while len(self.views) > 30:
            self.views.popitem(last=False)
        return vid


B: Backend | None = None
app = FastAPI(title="TokTidy", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def check_token(request: Request, call_next):
    if request.method != "OPTIONS" and request.url.path.startswith("/api/"):
        if request.headers.get("x-toktidy-token") != TOKEN:
            return JSONResponse({"detail": "bad token"}, status_code=403)
    return await call_next(request)


@app.exception_handler(ConfigError)
async def config_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(LookupError)
async def lookup_error(request, exc):
    if isinstance(exc, KeyError):
        B.log(traceback.format_exc())
        return JSONResponse({"detail": f"Internal error: {exc!r}"}, status_code=500)
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(Exception)
async def any_error(request, exc):
    B.log(f"Error in {request.url.path}:\n{traceback.format_exc()}")
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


# ---------------------------------------------------------------- state / setup

@app.get("/api/state")
def state():
    return {
        "version": __version__,
        "ready": B.paths is not None,
        "error": B.error,
        "paths": {p: str(B.paths.part(p)) for p in cache.ALL_PARTS} if B.paths else B.settings["paths"],
        "models": {k: {"label": m.get("label", k), "enabled": bool(m.get("enabled"))}
                   for k, m in B.settings["models"].items()},
        "default_model": B.settings.get("default_model"),
        "transcription": bool(B.settings["transcription"].get("enabled")),
        "app": B.settings["app"],
        "indexing": B.indexing(),
    }


@app.post("/api/setup/init")
def setup_init(p: dict = Body(...)):
    if not p.get("cache"):
        raise ConfigError("Choose a folder for the cache.")
    cache.init_cache(Path(p["cache"]), force=bool(p.get("force")))
    B.reload()
    B.log(f"Library created in {p['cache']}")
    return state()


@app.post("/api/cache/relink")
def cache_relink(p: dict = Body(...)):
    if B.indexing():
        raise ConfigError("Stop indexing first.")
    dest = cache.relink_cache(p["part"], Path(p["path"]))
    B.reload()
    B.log(f"Relinked {p['part']} to {dest}")
    return state()


@app.post("/api/cache/move")
def cache_move(p: dict = Body(...)):
    if B.indexing() or B.busy_task():
        raise ConfigError("Wait for indexing / the current task to finish first.")
    B.need_library()

    def work(say):
        if B.search_conn:
            B.search_conn.close()
            B.search_conn = None
        try:
            dest = cache.move_cache(p["part"], Path(p["dest"]), progress=say)
            say(f"Done. {p['part']} is now at {dest}")
            return str(dest)
        finally:
            B.reload()

    B.task = Task(B, f"Move {p['part']}", work)
    B.task.start()
    return {"started": True}


@app.post("/api/cache/measure")
def cache_measure():
    B.need_library()
    if B.busy_task():
        raise ConfigError("Another task is running.")

    def work(say):
        out = {}
        for part in cache.ALL_PARTS:
            path = B.paths.part(part)
            if part == "database":
                size = sum(Path(str(path) + s).stat().st_size for s in ("", "-wal") if Path(str(path) + s).exists())
                out[part] = {"path": str(path), "files": 1, "bytes": size}
            else:
                say(f"Measuring {part} ...")
                n, size = cache.dir_size(path)
                out[part] = {"path": str(path), "files": n, "bytes": size}
        say("Done.")
        return out

    B.task = Task(B, "Measure cache", work)
    B.task.start()
    return {"started": True}


@app.post("/api/cleanup")
def cleanup():
    B.need_library()
    if B.indexing() or B.busy_task():
        raise ConfigError("Wait for indexing / the current task to finish first.")

    def work(say):
        conn = db.connect(B.paths.database)
        try:
            n = scanner.purge_missing(conn, B.paths)
            say(f"Forgot {n:,} clips that no longer exist.")
        finally:
            conn.close()
        return n

    B.task = Task(B, "Clean up", work)
    B.task.start()
    return {"started": True}


@app.get("/api/task")
def task_status():
    return B.task.status if B.task else None


# ---------------------------------------------------------------- folders

def _folder_stats(conn):
    s = B.settings
    need = [db.META, db.FRAMES, db.PREVIEW, db.ANALYSIS] + [db.embed_stage(k) for k in enabled_models(s)]
    if s["transcription"].get("enabled"):
        need.append(db.TRANSCRIBE)
    stats = {}
    for r in conn.execute("SELECT folder_id, COUNT(*) n FROM videos WHERE present=1 GROUP BY folder_id"):
        stats[r[0]] = {"clips": r[1], "indexed": 0, "failed": 0, "searchable": 0}
    q = (f"SELECT v.folder_id, COUNT(*) FROM videos v WHERE v.present=1 AND "
         f"(SELECT COUNT(*) FROM stages s WHERE s.video_id=v.id AND s.status='done' "
         f"AND s.stage IN ({','.join('?' * len(need))})) = ? GROUP BY v.folder_id")
    for fid, n in conn.execute(q, [*need, len(need)]):
        stats.setdefault(fid, {"clips": 0, "failed": 0, "searchable": 0})["indexed"] = n
    for fid, n in conn.execute(
        "SELECT v.folder_id, COUNT(DISTINCT v.id) FROM stages s JOIN videos v ON v.id=s.video_id "
        "WHERE s.status='failed' AND v.present=1 GROUP BY v.folder_id"
    ):
        stats.setdefault(fid, {"clips": 0, "indexed": 0, "searchable": 0})["failed"] = n
    try:
        search_keys = resolve_models(s)
    except ConfigError:
        search_keys = [k for k in s["models"]][:1]
    # "Both models": a clip counts as searchable once any of them has it
    embed = [db.embed_stage(k) for k in search_keys]
    for fid, n in conn.execute(
        "SELECT v.folder_id, COUNT(DISTINCT v.id) FROM stages s JOIN videos v ON v.id=s.video_id "
        f"WHERE s.stage IN ({','.join('?' * len(embed))}) AND s.status='done' AND v.present=1 GROUP BY v.folder_id",
        embed,
    ):
        stats.setdefault(fid, {"clips": 0, "indexed": 0, "failed": 0})["searchable"] = n
    return stats


@app.get("/api/folders")
def folders():
    conn = B.conn()
    try:
        stats = _folder_stats(conn)
        out = []
        for f in db.all_folders(conn):
            st = stats.get(f["id"], {})
            out.append({
                "id": f["id"], "name": f["name"], "path": f["path"], "online": bool(f["online"]),
                "scanned": f["last_scan_at"] is not None,
                "clips": st.get("clips", 0), "indexed": st.get("indexed", 0),
                "failed": st.get("failed", 0), "searchable": st.get("searchable", 0),
            })
        return out
    finally:
        conn.close()


def _scan(conn, fid):
    f = conn.execute("SELECT * FROM folders WHERE id=?", (fid,)).fetchone()
    res = scanner.scan_folder(conn, B.paths, B.settings, f)
    B.log(res.summary())
    return res


@app.post("/api/folders/add")
def folders_add(p: dict = Body(...)):
    conn = B.conn()
    added = []
    try:
        paths = list(p.get("paths") or [])
        skipped = 0
        if p.get("parent"):
            parent = Path(p["parent"])
            exts = {e.lower() for e in B.settings["video_extensions"]}
            for x in sorted(parent.iterdir(), key=lambda d: d.name.lower()):
                if not x.is_dir() or re.sub(r"[\s_\-]", "", x.name.lower()) == "toktidycache":
                    continue
                try:
                    has_video = any(e.is_file() and os.path.splitext(e.name)[1].lower() in exts
                                    for e in os.scandir(x))
                except OSError:
                    has_video = False
                if has_video and not (x / cache.ID_FILENAME).exists():
                    paths.append(str(x))
                else:
                    skipped += 1
        for path in paths:
            if not Path(path).is_dir():
                continue
            fid, created = db.add_folder(conn, Path(path))
            if created:
                added.append(fid)
                if not B.indexing():
                    _scan(conn, fid)
        B.log(f"Added {len(added)} folder(s)")
        return {"added": added, "skipped": skipped}
    finally:
        conn.close()


@app.post("/api/folders/scan")
def folders_scan(p: dict = Body(default={})):
    if B.indexing():
        raise ConfigError("Indexing is running; it checks folders automatically.")
    conn = B.conn()
    try:
        ids = p.get("folder_ids") or [f["id"] for f in db.all_folders(conn)]
        return {"results": [_scan(conn, i).summary() for i in ids]}
    finally:
        conn.close()


@app.post("/api/folders/{fid}/relink")
def folder_relink(fid: int, p: dict = Body(...)):
    if B.indexing():
        raise ConfigError("Stop indexing first.")
    conn = B.conn()
    try:
        f = conn.execute("SELECT * FROM folders WHERE id=?", (fid,)).fetchone()
        res = scanner.relink_folder(conn, B.paths, B.settings, f, Path(p["path"]))
        B.log("Relinked folder: " + res.summary())
        return {"summary": res.summary()}
    finally:
        conn.close()


@app.post("/api/folders/{fid}/remove")
def folder_remove(fid: int):
    if B.indexing():
        raise ConfigError("Stop indexing first.")
    conn = B.conn()
    try:
        conn.execute("UPDATE videos SET present=0 WHERE folder_id=?", (fid,))
        scanner.purge_missing(conn, B.paths, fid)
        conn.execute("DELETE FROM folders WHERE id=?", (fid,))
        conn.commit()
        B.views.clear()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/used/reset")
def used_reset(p: dict = Body(default={})):
    conn = B.conn()
    try:
        ids = p.get("folder_ids")
        if ids:
            n = conn.execute(
                f"UPDATE videos SET used=0, used_at=NULL WHERE used=1 AND folder_id IN ({','.join('?' * len(ids))})",
                ids).rowcount
        else:
            n = conn.execute("UPDATE videos SET used=0, used_at=NULL WHERE used=1").rowcount
        conn.commit()
        return {"cleared": n}
    finally:
        conn.close()


@app.post("/api/clips/used")
def clips_used(p: dict = Body(...)):
    conn = B.conn()
    try:
        used = 1 if p.get("used", True) else 0
        ids = [int(i) for i in p.get("ids", [])]
        conn.executemany("UPDATE videos SET used=?, used_at=? WHERE id=?",
                         [(used, time.time() if used else None, i) for i in ids])
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/clips/by-paths")
def clips_by_paths(p: dict = Body(...)):
    conn = B.conn()
    try:
        out = []
        for path in p.get("paths", []):
            row = db.find_video_by_path(conn, Path(path))
            if row:
                out.append(row["id"])
        return {"ids": out}
    finally:
        conn.close()


@app.post("/api/clips/get")
def clips_get(p: dict = Body(...)):
    conn = B.conn()
    try:
        hits = [Hit(video_id=int(i), score=0.0) for i in p.get("ids", [])]
        return {"items": _clip_dicts(conn, attach_rows(conn, hits))}
    finally:
        conn.close()


# ---------------------------------------------------------------- views (browse + search)

def _folder_ids(p):
    ids = p.get("folders")
    return [int(i) for i in ids] if ids else None


@app.post("/api/view")
def make_view(p: dict = Body(...)):
    B.need_library()
    mode = p.get("mode", "browse")
    fids = _folder_ids(p)
    hide_used = bool(p.get("hide_used"))
    model = p.get("model") or B.settings.get("default_model")
    keys = resolve_models(B.settings, model) if mode in ("text", "image", "similar") else None
    t = time.time()
    note = None
    conn = B.conn()
    try:
        if mode == "browse":
            ids = browse_ids(conn, fids, hide_used, p.get("sort", "name"), bool(p.get("desc")), p.get("seed"))
            hits = ids  # plain ids; turned into Hits when a page is requested
        elif mode == "audio":
            hits = audio_search(conn, p.get("query", ""), fids, MAX_RESULTS, hide_used)
            if not B.settings["transcription"].get("enabled"):
                note = "Transcription is turned off in Settings, so only already-transcribed clips can match."
        elif mode == "color":
            hits = color_search(conn, parse_color(p.get("color", "")), fids, MAX_RESULTS, hide_used)
        else:
            s = B.searcher
            with B.search_lock:
                if mode == "text":
                    hits = s.text(keys, p.get("query", ""), fids, p.get("exclude") or (),
                                  MAX_RESULTS, hide_used, raw=True)
                elif mode == "image":
                    if p.get("image_data"):
                        from PIL import Image
                        data = p["image_data"].split(",", 1)[-1]
                        img = Image.open(io.BytesIO(base64.b64decode(data)))
                        img.load()
                    elif p.get("image_path"):
                        img = p["image_path"]
                    else:
                        raise ConfigError("Drop or paste an image first.")
                    hits = s.image(keys, img, fids, p.get("exclude") or (), MAX_RESULTS, hide_used, raw=True)
                elif mode == "similar":
                    ids = [int(i) for i in p.get("clip_ids", [])]
                    if not ids:
                        raise ConfigError("No clip chosen.")
                    hits = s.similar(keys, ids, fids, MAX_RESULTS, hide_used, raw=True)
                else:
                    raise ConfigError(f"Unknown mode {mode}")
                if mode in ("text", "image"):
                    allowed = len(allowed_video_ids(conn, fids, hide_used))
                    if len(hits) < allowed:
                        note = (f"{allowed - len(hits):,} clip(s) in this selection aren't searchable "
                                "with the chosen model yet (still being indexed).")
        took = time.time() - t
        vid = B.store_view(mode, hits, {"model": model})
        return {"view_id": vid, "total": len(hits), "took": round(took, 3), "mode": mode, "note": note}
    finally:
        conn.close()


def _clip_dicts(conn, hits):
    if not hits:
        return []
    ids = [h.video_id for h in hits]
    stages: dict[int, set] = {}
    failed: dict[int, str] = {}
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        for vid, stage, status, error in conn.execute(
            f"SELECT video_id, stage, status, error FROM stages WHERE "
            f"video_id IN ({','.join('?' * len(part))}) AND (status='failed' OR stage IN ('preview','frames'))",
            part):
            if status == "done":
                stages.setdefault(vid, set()).add(stage)
            else:
                failed.setdefault(vid, f"{stage}: {error}")
    out = []
    for h in hits:
        r = h.row
        st = stages.get(h.video_id, set())
        path = Path(r["folder_path"]) / r["filename"]
        fi = h.frame_idx if h.frame_idx is not None else 0
        out.append({
            "id": h.video_id,
            "filename": r["filename"],
            "path": str(path),
            "folder_id": r["folder_id"],
            "folder": r["folder_name"],
            "ext": (r["ext"] or "").lstrip("."),
            "size": r["size"],
            "duration": r["duration"],
            "width": r["width"],
            "height": r["height"],
            "fps": r["fps"],
            "has_audio": bool(r["has_audio"]) if r["has_audio"] is not None else None,
            "used": bool(r["used"]),
            "failed": failed.get(h.video_id),
            "mtime": r["mtime"],
            "preview": cache.preview_path(B.paths, h.video_id).as_uri() if "preview" in st else None,
            "poster": cache.frame_path(B.paths, h.video_id, fi).as_uri() if "frames" in st else None,
            "icon": str(cache.frame_path(B.paths, h.video_id, 0)) if "frames" in st else None,
            "score": round(h.score, 4) if h.score else None,
            "time": h.time,
            "snippet": h.snippet,
            "brightness": r["brightness"],
            "motion": r["motion"],
            "pace": r["pace"],
            "colors": json.loads(r["colors"]) if r["colors"] else None,
        })
    return out


@app.get("/api/view/{view_id}")
def view_page(view_id: str, offset: int = 0, limit: int = 500):
    v = B.views.get(view_id)
    if v is None:
        raise HTTPException(410, "This result list expired; search again.")
    hits = v["hits"][offset:offset + max(1, min(limit, 5000))]
    if hits and not isinstance(hits[0], Hit):
        hits = [Hit(video_id=i, score=0.0) for i in hits]
    conn = B.conn()
    try:
        return {"total": len(v["hits"]), "offset": offset, "items": _clip_dicts(conn, attach_rows(conn, list(hits)))}
    finally:
        conn.close()


# ---------------------------------------------------------------- indexing

@app.post("/api/index/start")
def index_start(p: dict = Body(default={})):
    B.need_library()
    if B.indexing():
        raise ConfigError("Indexing is already running.")
    if B.busy_task():
        raise ConfigError("Wait for the current task to finish.")
    ids = p.get("folder_ids") or None
    B.job = IndexJob(B, ids, p.get("only"), p.get("models"), p.get("limit"))
    B.job.start()
    B.log(f"Indexing started ({'selected folders' if ids else 'all folders'})")
    return {"started": True}


@app.post("/api/index/stop")
def index_stop():
    if B.job:
        B.job.stop()
    return {"ok": True}


@app.get("/api/index/status")
def index_status():
    s = B.job.snapshot() if B.job else None
    return {"job": s, "log": list(B.log_lines)[-60:]}


@app.get("/api/failed")
def failed():
    conn = B.conn()
    try:
        rows = conn.execute(
            "SELECT v.id, v.filename, f.name folder, f.path, group_concat(s.stage, ', ') stages, "
            "max(s.error) error FROM stages s JOIN videos v ON v.id=s.video_id "
            "JOIN folders f ON f.id=v.folder_id WHERE s.status='failed' AND v.present=1 "
            "GROUP BY v.id ORDER BY f.name, v.filename LIMIT 2000").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/failed/retry")
def failed_retry():
    conn = B.conn()
    try:
        n = conn.execute("DELETE FROM stages WHERE status='failed'").rowcount
        conn.commit()
        return {"reset": n}
    finally:
        conn.close()


@app.post("/api/models/reset")
def model_reset(p: dict = Body(...)):
    if B.indexing():
        raise ConfigError("Stop indexing first.")
    key = p["model"]
    from .embstore import EmbeddingStore
    f = cache.embeddings_file(B.paths, key)
    if f.exists():
        st = EmbeddingStore(f)
        st.clear()
        st.close()
    conn = B.conn()
    try:
        n = conn.execute("DELETE FROM stages WHERE stage=?", (db.embed_stage(key),)).rowcount
        conn.commit()
    finally:
        conn.close()
    B.models.unload(key)
    B.reload()
    return {"cleared": n}


# ---------------------------------------------------------------- settings / diagnostics

@app.get("/api/settings")
def get_settings():
    return B.settings


@app.post("/api/settings")
def set_settings(p: dict = Body(...)):
    p.pop("paths", None)  # paths change only through init / move / relink
    current = load_settings()
    before_models = json.dumps(current["models"], sort_keys=True)
    before_tr = json.dumps(current["transcription"], sort_keys=True)
    merged = _deep_merge(current, p)
    save_settings(merged)
    if json.dumps(merged["models"], sort_keys=True) != before_models or \
            json.dumps(merged["transcription"], sort_keys=True) != before_tr:
        if not B.indexing():
            B.models.unload_all()
    with B.lock:
        B.settings = load_settings()
        B.models.settings = B.settings
        if B.searcher:
            B.searcher.settings = B.settings
    return B.settings


@app.get("/api/diagnostics")
def diagnostics(whisper: bool = False):
    from .diagnostics import run_checks
    checks = run_checks(whisper)
    info = {"checks": checks, "log_file": str(LOG_DIR / "backend.log")}
    if B.paths:
        conn = B.conn()
        try:
            info["clips"] = conn.execute("SELECT COUNT(*) FROM videos WHERE present=1").fetchone()[0]
            info["folders"] = conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
            info["stages"] = [list(r) for r in conn.execute(
                "SELECT stage, status, COUNT(*) FROM stages GROUP BY stage, status ORDER BY stage")]
            info["failed_samples"] = [dict(r) for r in conn.execute(
                "SELECT s.stage, s.error, v.filename, v.vcodec FROM stages s JOIN videos v ON v.id=s.video_id "
                "WHERE s.status='failed' LIMIT 15")]
        finally:
            conn.close()
    info["settings"] = {k: B.settings[k] for k in ("indexing", "models", "transcription", "app", "paths")}
    info["models_loaded"] = B.models.loaded_keys()
    try:
        import torch
        if torch.cuda.is_available():
            info["vram_used_gb"] = round(torch.cuda.memory_allocated() / 2**30, 2)
    except Exception:
        pass
    info["log"] = list(B.log_lines)[-200:]
    return info


@app.get("/api/ping")
def ping():
    return {"ok": True}


# ---------------------------------------------------------------- entry

def main():
    global B
    import uvicorn
    B = Backend()
    B.log(f"TokTidy backend {__version__} starting (Python {sys.version.split()[0]})")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", int(os.environ.get("TOKTIDY_PORT", "0"))))
    sock.listen(128)  # accept (queue) connections right away, before announcing readiness
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    print(f"TOKTIDY_READY {port}", flush=True)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
