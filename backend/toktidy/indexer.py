"""The indexer.

For each selected folder:
  1. scan       - find new / changed / moved clips (seconds)
  2. decode     - one ffmpeg pass per clip: metadata, frames, preview, colour/motion
  3. embed      - AI vectors for every enabled search model (GPU)
  4. transcribe - speech to text (GPU)

Every stage is recorded per clip, so indexing can be stopped at any time
(Ctrl+C) and picks up exactly where it left off. Enabling another model or
transcription later only runs that missing stage.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import cache, db, media, scanner
from .config import ConfigError, enabled_models
from .embstore import EmbeddingStore
from .models import ModelManager, model_fingerprint

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None


class Stopped(Exception):
    pass


@dataclass
class DecodeResult:
    video_id: int
    info: media.ProbeInfo | None = None
    frame_times: list | None = None
    analysis: dict | None = None
    preview_ok: bool = False
    audio_path: Path | None = None
    error: str | None = None
    wanted: tuple = ()
    qhash: str | None = None


def _progress(total, desc):
    if tqdm is None:
        class _Dummy:
            def update(self, n=1): pass
            def set_postfix_str(self, s): pass
            def close(self): pass
        return _Dummy()
    return tqdm(total=total, desc=desc, unit="clip", dynamic_ncols=True, leave=True)


class IndexLock:
    def __init__(self, paths):
        self.path = cache.lock_path(paths)

    def __enter__(self):
        if self.path.exists():
            try:
                import psutil
                pid = int(self.path.read_text().strip())
                if psutil.pid_exists(pid) and pid != os.getpid():
                    raise ConfigError("Indexing is already running in another window.")
            except (ValueError, OSError):
                pass
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


class Indexer:
    def __init__(self, conn, paths: cache.Paths, settings: dict, log=print,
                 models: ModelManager | None = None, progress=None, on_folder=None):
        self.conn, self.paths, self.settings, self.log = conn, paths, settings, log
        self.stop = threading.Event()
        self.models = models or ModelManager(settings, log)
        self.progress = progress or _progress
        self.on_folder = on_folder or (lambda n, total, folder: None)
        self.audio_files: dict[int, Path] = {}
        self._stores: dict[str, EmbeddingStore] = {}

    # ---------------------------------------------------------------- helpers
    def store(self, key: str) -> EmbeddingStore:
        if key not in self._stores:
            fp = model_fingerprint(key, self.settings["models"][key])
            self._stores[key] = EmbeddingStore(cache.embeddings_file(self.paths, key), fp)
        return self._stores[key]

    def close(self):
        for s in self._stores.values():
            s.close()
        self._stores.clear()
        self.models.unload_all()
        for p in self.audio_files.values():
            Path(p).unlink(missing_ok=True)
        self.audio_files.clear()

    def _check_stop(self):
        if self.stop.is_set():
            raise Stopped()

    def pending(self, folder_id, stages, requires=None, limit=None):
        """Clips in a folder missing ANY of `stages` (and with `requires` done)."""
        stages = [stages] if isinstance(stages, str) else list(stages)
        missing = " OR ".join(
            "NOT EXISTS (SELECT 1 FROM stages s WHERE s.video_id=v.id AND s.stage=?)" for _ in stages
        )
        q = f"SELECT v.* FROM videos v WHERE v.folder_id=? AND v.present=1 AND ({missing})"
        args = [folder_id, *stages]
        if requires:
            q += (" AND EXISTS (SELECT 1 FROM stages r WHERE r.video_id=v.id "
                  "AND r.stage=? AND r.status='done')")
            args.append(requires)
        q += " ORDER BY v.filename COLLATE NOCASE"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.conn.execute(q, args).fetchall()

    def _limited_ids(self, folder_id, limit):
        if not limit:
            return None
        rows = self.conn.execute(
            "SELECT id FROM videos WHERE folder_id=? AND present=1 "
            "ORDER BY filename COLLATE NOCASE LIMIT ?", (folder_id, int(limit))
        ).fetchall()
        return {r["id"] for r in rows}

    # ---------------------------------------------------------------- entry
    def run(self, folders, only: str | None = None, model_keys=None, limit=None):
        models = model_keys or enabled_models(self.settings)
        for k in models:
            if k not in self.settings["models"]:
                raise ConfigError(f"Unknown model '{k}'.")
        transcribe_on = bool(self.settings["transcription"].get("enabled"))
        started = time.time()
        with IndexLock(self.paths):
            try:
                for n, folder in enumerate(folders, 1):
                    self._check_stop()
                    self.on_folder(n, len(folders), folder)
                    self.log(f"\n[{n}/{len(folders)}] {folder['name']}  ({folder['path']})")
                    res = scanner.scan_folder(self.conn, self.paths, self.settings, folder)
                    self.log("  " + res.summary())
                    if res.offline:
                        continue
                    allowed = self._limited_ids(folder["id"], limit)

                    if only in (None, "decode"):
                        self.decode_folder(folder, allowed, transcribe_on and only is None)

                    gpu_jobs = []
                    if only in (None, "embed"):
                        gpu_jobs += [("embed", k) for k in models]
                    if only in (None, "transcribe") and transcribe_on:
                        gpu_jobs.append(("transcribe", "whisper"))
                    # run whatever model is already in VRAM first (fewer reloads)
                    loaded = self.models.loaded_keys()
                    gpu_jobs.sort(key=lambda j: 0 if j[1] in loaded else 1)
                    for kind, key in gpu_jobs:
                        self._check_stop()
                        if kind == "embed":
                            self.embed_folder(folder, key, allowed)
                        else:
                            self.transcribe_folder(folder, allowed)
                    self._drop_audio()
            except Stopped:
                self.log("\nStopped. Everything finished so far is saved; run the same command to continue.")
                return False
            finally:
                self.close()
        mins = (time.time() - started) / 60
        self.log(f"\nDone in {mins:.1f} min.")
        return True

    def _drop_audio(self):
        for p in self.audio_files.values():
            Path(p).unlink(missing_ok=True)
        self.audio_files.clear()

    # ---------------------------------------------------------------- decode
    def decode_folder(self, folder, allowed, want_audio: bool):
        rows = self.pending(folder["id"], db.DECODE_STAGES)
        if allowed is not None:
            rows = [r for r in rows if r["id"] in allowed]
        if not rows:
            return
        workers = max(1, int(self.settings["indexing"].get("decode_workers", 1)))
        bar = self.progress(len(rows), "Making previews & frames")
        failed = 0
        todo = list(rows)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            running = {}
            while todo or running:
                while todo and len(running) < workers and not self.stop.is_set():
                    r = todo.pop(0)
                    done = db.stage_statuses(self.conn, r["id"])
                    missing = tuple(s for s in db.DECODE_STAGES if s not in done)
                    want_a = want_audio and db.TRANSCRIBE not in done
                    running[pool.submit(self._decode_one, folder, r, missing, want_a)] = r
                if not running:
                    break
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    running.pop(fut)
                    res: DecodeResult = fut.result()
                    if res.error == "__stopped__":
                        continue
                    self._save_decode(res)
                    if res.error:
                        failed += 1
                        bar.set_postfix_str(f"{failed} failed")
                    bar.update(1)
                if self.stop.is_set():
                    todo.clear()
        bar.close()
        self._check_stop()

    def _decode_one(self, folder, row, missing, want_audio) -> DecodeResult:
        vid = row["id"]
        res = DecodeResult(video_id=vid, wanted=missing)
        if self.stop.is_set():
            res.error = "__stopped__"
            return res
        src = Path(folder["path"]) / row["filename"]
        s = self.settings["indexing"]
        try:
            if not row["qhash"]:
                res.qhash = scanner.quick_hash(src, row["size"])
            info = media.probe(src, s.get("low_priority", True))
            res.info = info
            req = media.DecodeRequest(
                src=src, info=info,
                want_frames=db.FRAMES in missing,
                want_analysis=db.ANALYSIS in missing,
                want_preview=db.PREVIEW in missing,
                want_audio=want_audio and info.has_audio,
                preview_out=cache.preview_path(self.paths, vid),
                tmp_prefix=self.paths.temp / str(vid),
                settings=self.settings,
            )
            if not (req.want_frames or req.want_analysis or req.want_preview):
                return res
            out = media.run_decode(req)
            res.preview_ok = out.preview_written
            res.audio_path = out.audio_path
            if out.frames is not None:
                if req.want_analysis:
                    b = media.brightness(out.frames)
                    colors = media.dominant_colors(out.frames)
                    motion, pace = media.motion_scores(out.gray, info.duration)
                    res.analysis = {"brightness": b, "colors": colors, "motion": motion, "pace": pace}
                if req.want_frames:
                    keep = media.dedupe_frames(out.frames, out.frame_times,
                                               float(s.get("dedupe_threshold", 0)))
                    media.save_frames(out.frames, keep, cache.frame_dir(self.paths, vid),
                                      int(s.get("frame_jpeg_quality", 85)))
                    res.frame_times = [out.frame_times[i] for i in keep]
        except (media.MediaError, OSError, ValueError) as e:
            res.error = str(e)[:500]
        except ConfigError:
            raise
        except Exception as e:  # never let one bad clip stop the run
            res.error = f"{type(e).__name__}: {e}"[:500]
        return res

    def _save_decode(self, res: DecodeResult):
        c = self.conn
        vid = res.video_id
        if res.qhash:
            c.execute("UPDATE videos SET qhash=? WHERE id=?", (res.qhash, vid))
        if res.info is not None:
            i = res.info
            c.execute(
                "UPDATE videos SET duration=?, width=?, height=?, fps=?, vcodec=?, has_audio=? WHERE id=?",
                (i.duration, i.width, i.height, i.fps, i.vcodec, int(i.has_audio), vid),
            )
            db.set_stage(c, vid, db.META)
        if res.error:
            for st in res.wanted:
                if st != db.META or res.info is None:
                    db.set_stage(c, vid, st, "failed", res.error)
            c.commit()
            return
        if res.frame_times is not None:
            c.execute("UPDATE videos SET frame_times=? WHERE id=?", (json.dumps(res.frame_times), vid))
            db.set_stage(c, vid, db.FRAMES)
            # new frames invalidate old embeddings
            for key in list(self.settings["models"]):
                db.clear_stages(c, vid, [db.embed_stage(key)])
        if res.analysis is not None:
            a = res.analysis
            c.execute(
                "UPDATE videos SET brightness=?, motion=?, pace=?, colors=? WHERE id=?",
                (a["brightness"], a["motion"], a["pace"], json.dumps(a["colors"]), vid),
            )
            db.set_stage(c, vid, db.ANALYSIS)
        if res.preview_ok:
            db.set_stage(c, vid, db.PREVIEW)
        if res.audio_path is not None:
            self.audio_files[vid] = res.audio_path
        c.commit()

    # ---------------------------------------------------------------- embed
    def _load_frames(self, row):
        times = json.loads(row["frame_times"] or "[]")
        imgs = []
        for i in range(len(times)):
            p = cache.frame_path(self.paths, row["id"], i)
            with Image.open(p) as im:
                imgs.append(im.convert("RGB"))
        return imgs

    def embed_folder(self, folder, key, allowed):
        stage = db.embed_stage(key)
        rows = self.pending(folder["id"], stage, requires=db.FRAMES)
        if allowed is not None:
            rows = [r for r in rows if r["id"] in allowed]
        if not rows:
            return
        store = self.store(key)
        with self.models.lock:
            self.models.embedder(key)  # load up front so progress reflects real work
        batch_size = max(1, int(self.settings["indexing"].get("embed_batch_size", 32)))
        label = self.settings["models"][key].get("label", key)
        bar = self.progress(len(rows), f"AI search data ({label})")

        buf_imgs, buf_owner = [], []          # pending images and their clip id
        parts: dict[int, list] = {}           # clip id -> list of vector arrays
        need: dict[int, int] = {}             # clip id -> frames expected

        def flush():
            if not buf_imgs:
                return
            with self.models.lock:
                model = self.models.embedder(key)
                try:
                    vecs = model.encode_images(buf_imgs)
                except RuntimeError as e:
                    if "out of memory" in str(e).lower() and len(buf_imgs) > 1:
                        self.log("  ! GPU out of memory; retrying in smaller batches "
                                 "(consider lowering embed_batch_size)")
                        from .models import free_vram
                        free_vram()
                        vecs = np.concatenate([
                            model.encode_images(buf_imgs[i:i + 4]) for i in range(0, len(buf_imgs), 4)
                        ])
                    else:
                        raise
            for owner, v in zip(buf_owner, vecs):
                parts[owner].append(v)
            buf_imgs.clear()
            buf_owner.clear()
            finished = [vid for vid, n in need.items() if len(parts[vid]) == n]
            for vid in finished:
                store.put(vid, np.stack(parts.pop(vid)))
                need.pop(vid)
                db.set_stage(self.conn, vid, stage)
            store.commit()
            self.conn.commit()
            bar.update(len(finished))

        with ThreadPoolExecutor(max_workers=2) as loader:
            for chunk_start in range(0, len(rows), 16):
                self._check_stop_after(flush, bar)
                chunk = rows[chunk_start:chunk_start + 16]
                futures = [(r, loader.submit(self._load_frames, r)) for r in chunk]
                for r, fut in futures:
                    try:
                        imgs = fut.result()
                    except OSError:
                        # frames vanished from the cache: regenerate on the next run
                        db.clear_stages(self.conn, r["id"], [db.FRAMES])
                        self.conn.commit()
                        bar.update(1)
                        continue
                    if not imgs:
                        db.set_stage(self.conn, r["id"], stage, "failed", "no frames")
                        bar.update(1)
                        continue
                    parts[r["id"]] = []
                    need[r["id"]] = len(imgs)
                    for im in imgs:
                        buf_imgs.append(im)
                        buf_owner.append(r["id"])
                        if len(buf_imgs) >= batch_size:
                            flush()
            flush()
        bar.close()

    def _check_stop_after(self, flush, bar):
        if self.stop.is_set():
            flush()
            bar.close()
            raise Stopped()

    # ---------------------------------------------------------------- transcribe
    def transcribe_folder(self, folder, allowed):
        rows = self.pending(folder["id"], db.TRANSCRIBE, requires=db.META)
        if allowed is not None:
            rows = [r for r in rows if r["id"] in allowed]
        if not rows:
            return
        use_fts = db.has_fts(self.conn)
        bar = self.progress(len(rows), "Transcribing speech")
        failed = 0
        for r in rows:
            if self.stop.is_set():
                bar.close()
                raise Stopped()
            vid = r["id"]
            result = {"text": "", "segments": [], "language": None, "language_prob": None}
            try:
                if r["has_audio"]:
                    audio = self.audio_files.pop(vid, None)
                    src = audio if audio and Path(audio).exists() else Path(folder["path"]) / r["filename"]
                    try:
                        with self.models.lock:
                            result = self.models.transcriber().transcribe(src)
                    finally:
                        if audio:
                            Path(audio).unlink(missing_ok=True)
            except Exception as e:
                db.set_stage(self.conn, vid, db.TRANSCRIBE, "failed", f"{type(e).__name__}: {e}"[:500])
                self.conn.commit()
                failed += 1
                bar.set_postfix_str(f"{failed} failed")
                bar.update(1)
                continue
            self.conn.execute(
                "INSERT OR REPLACE INTO transcripts(video_id, language, language_prob, text, segments) "
                "VALUES(?,?,?,?,?)",
                (vid, result["language"], result["language_prob"], result["text"],
                 json.dumps(result["segments"])),
            )
            if use_fts:
                self.conn.execute("DELETE FROM transcripts_fts WHERE rowid=?", (vid,))
                if result["text"]:
                    self.conn.execute("INSERT INTO transcripts_fts(rowid, text) VALUES(?,?)",
                                      (vid, result["text"]))
            db.set_stage(self.conn, vid, db.TRANSCRIBE)
            self.conn.commit()
            bar.update(1)
        bar.close()
