"""TokTidy command line.  Run `toktidy help` for a list of commands."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import __version__, cache, db, scanner
from .config import ConfigError, enabled_models, load_settings, save_settings, settings_path


# ---------------------------------------------------------------- helpers

def open_file(path: Path):
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def library():
    settings = load_settings()
    paths = cache.get_paths(settings)
    conn = db.connect(paths.database)
    db.create_schema(conn)  # adds any new tables from newer versions
    return settings, paths, conn


def pick_folders(conn, refs, allow_all_default=False):
    if not refs or refs == ["all"]:
        if refs == ["all"] or allow_all_default:
            return db.all_folders(conn)
        return []
    return [db.resolve_folder(conn, r) for r in refs]


def folder_ids_arg(conn, refs):
    if not refs:
        return None
    return [f["id"] for f in pick_folders(conn, refs)]


def fmt_dur(t):
    return f"{int(t // 60)}:{int(t % 60):02d}" if t is not None else "  -  "


def print_hits(hits, show_score=True):
    if not hits:
        print("No matches.")
        return
    for i, h in enumerate(hits, 1):
        r = h.row
        at = f" @{fmt_dur(h.time)}" if h.time is not None else ""
        score = f"{h.score:7.3f}  " if show_score else ""
        used = " [used]" if r.get("used") else ""
        print(f"{i:4}. {score}{r['folder_name'][:22]:22}  {r['filename']}{at}{used}")
        if h.snippet:
            print(f"        “{h.snippet}”")


def resolve_clip(conn, ref: str) -> int:
    if ref.isdigit():
        return int(ref)
    row = db.find_video_by_path(conn, Path(ref))
    if not row:
        raise LookupError(f"{ref} is not an indexed clip.")
    return row["id"]


def install_ctrl_c(stop_event):
    state = {"n": 0}

    def handler(signum, frame):
        state["n"] += 1
        if state["n"] == 1:
            print("\n>> Stopping after the current clips finish... (press Ctrl+C again to quit immediately)",
                  flush=True)
            stop_event.set()
        else:
            print("\n>> Quitting now. Unfinished clips will be redone next time.", flush=True)
            os._exit(1)

    signal.signal(signal.SIGINT, handler)


def model_arg(settings, key):
    key = key or settings.get("default_model") or enabled_models(settings)[0]
    if key == "both":  # the command line uses one model at a time
        key = enabled_models(settings)[0]
    if key not in settings["models"]:
        raise ConfigError(f"Unknown model '{key}'. Choose from: {', '.join(settings['models'])}")
    return key


# ---------------------------------------------------------------- commands

def cmd_init(a):
    paths = cache.init_cache(
        Path(a.cache),
        {"frames": a.frames, "previews": a.previews, "embeddings": a.embeddings,
         "database_dir": a.database_dir},
        force=a.force,
    )
    print("Library created:")
    for part in cache.ALL_PARTS:
        print(f"  {part:10} {paths.part(part)}")
    print(f"Settings file: {settings_path()}")
    print('\nNext: toktidy add-folder "D:\\TikToks\\Folder 1"')


def cmd_doctor(a):
    from .diagnostics import run_checks
    results = run_checks(a.whisper)
    for status, msg in results:
        print(f"  {status:5} {msg}")
    ok = not any(st == "FAIL" for st, _ in results)
    print("\nAll good!" if ok else "\nSome checks failed - see README 'Troubleshooting'.")


def cmd_add_folder(a):
    _, _, conn = library()
    for p in a.paths:
        path = Path(p.strip('"')).resolve()
        if not path.is_dir():
            print(f"  skip  {path} (not a folder)")
            continue
        fid, created = db.add_folder(conn, path)
        print(f"  {'added ' if created else 'exists'}  [{fid}] {path}")
    print("\nIndex them with:  toktidy index --all   (or toktidy index <name or id>)")


def cmd_add_parent(a):
    _, _, conn = library()
    parent = Path(a.parent.strip('"')).resolve()
    subs = sorted([p for p in parent.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
    for p in subs:
        fid, created = db.add_folder(conn, p)
        print(f"  {'added ' if created else 'exists'}  [{fid}] {p}")
    print(f"\n{len(subs)} folders.")


def cmd_remove_folder(a):
    settings, paths, conn = library()
    f = db.resolve_folder(conn, a.folder)
    n = conn.execute("SELECT COUNT(*) FROM videos WHERE folder_id=?", (f["id"],)).fetchone()[0]
    if not a.yes:
        ans = input(f"Forget '{f['name']}' and delete its cache for {n:,} clips? Your videos are not touched. [y/N] ")
        if ans.strip().lower() != "y":
            return
    conn.execute("UPDATE videos SET present=0 WHERE folder_id=?", (f["id"],))
    scanner.purge_missing(conn, paths, f["id"])
    conn.execute("DELETE FROM folders WHERE id=?", (f["id"],))
    conn.commit()
    print("Removed.")


def _folder_stats(conn, settings):
    stages = [db.FRAMES, db.PREVIEW] + [db.embed_stage(k) for k in enabled_models(settings)]
    if settings["transcription"].get("enabled"):
        stages.append(db.TRANSCRIBE)
    stats = {}
    for r in conn.execute(
        "SELECT folder_id, COUNT(*) n FROM videos WHERE present=1 GROUP BY folder_id"
    ):
        stats[r["folder_id"]] = {"clips": r["n"]}
    for r in conn.execute(
        "SELECT v.folder_id, s.stage, s.status, COUNT(*) n FROM stages s "
        "JOIN videos v ON v.id=s.video_id WHERE v.present=1 GROUP BY v.folder_id, s.stage, s.status"
    ):
        d = stats.setdefault(r["folder_id"], {"clips": 0})
        d[(r["stage"], r["status"])] = r["n"]
    return stages, stats


def cmd_folders(a):
    settings, _, conn = library()
    stages, stats = _folder_stats(conn, settings)
    short = {db.FRAMES: "frames", db.PREVIEW: "preview", db.TRANSCRIBE: "audio"}
    for k in enabled_models(settings):
        short[db.embed_stage(k)] = k
    folders = db.all_folders(conn)
    if not folders:
        print('No folders yet. Add one with:  toktidy add-folder "D:\\TikToks\\Folder 1"')
        return
    head = f"{'id':>4}  {'folder':28} {'clips':>6}  " + "  ".join(f"{short[s]:>9}" for s in stages)
    print(head)
    print("-" * len(head))
    for f in folders:
        d = stats.get(f["id"], {"clips": 0})
        cells = []
        for s in stages:
            done = d.get((s, "done"), 0)
            bad = d.get((s, "failed"), 0)
            cell = "done" if d["clips"] and done == d["clips"] else f"{done}/{d['clips']}"
            if bad:
                cell += f" !{bad}"
            cells.append(f"{cell:>9}")
        name = f["name"] + ("  (offline)" if not f["online"] else "")
        print(f"{f['id']:>4}  {name[:28]:28} {d['clips']:>6}  " + "  ".join(cells))
    print("\n'!n' = failed clips (see `toktidy failed`). Folder counts refresh when you run `toktidy scan` or `toktidy index`.")


def cmd_scan(a):
    settings, paths, conn = library()
    for f in pick_folders(conn, a.folders, allow_all_default=True):
        print(scanner.scan_folder(conn, paths, settings, f).summary())


def cmd_index(a):
    settings, paths, conn = library()
    if not a.folders and not a.all:
        raise ConfigError("Say which folders to index, e.g.  toktidy index \"Folder 1\"   or   toktidy index --all")
    folders = db.all_folders(conn) if a.all else pick_folders(conn, a.folders)
    if not folders:
        raise ConfigError("No folders in the library yet. Use toktidy add-folder first.")
    if a.no_transcribe:
        settings["transcription"]["enabled"] = False
    models = a.model or None
    from .indexer import Indexer
    ix = Indexer(conn, paths, settings)
    install_ctrl_c(ix.stop)
    print("Indexing. Press Ctrl+C once to stop safely.")
    ix.run(folders, only=a.only, model_keys=models, limit=a.limit)


def cmd_status(a):
    cmd_folders(a)
    settings, paths, conn = library()
    total = conn.execute("SELECT COUNT(*) FROM videos WHERE present=1").fetchone()[0]
    missing = conn.execute("SELECT COUNT(*) FROM videos WHERE present=0").fetchone()[0]
    used = conn.execute("SELECT COUNT(*) FROM videos WHERE used=1 AND present=1").fetchone()[0]
    print(f"\n{total:,} clips in the library, {used:,} marked used, {missing:,} missing.")


def cmd_failed(a):
    _, _, conn = library()
    q = ("SELECT s.stage, s.error, v.filename, f.name folder FROM stages s JOIN videos v ON v.id=s.video_id "
         "JOIN folders f ON f.id=v.folder_id WHERE s.status='failed'")
    args = []
    ids = folder_ids_arg(conn, a.folders)
    if ids:
        q += f" AND v.folder_id IN ({','.join('?' * len(ids))})"
        args = ids
    rows = conn.execute(q + " ORDER BY f.name, v.filename", args).fetchall()
    if not rows:
        print("No failed clips.")
        return
    for r in rows:
        print(f"[{r['folder']}] {r['filename']}  ({r['stage']})\n      {r['error']}")
    print(f"\n{len(rows)} failures. Retry with:  toktidy retry")


def cmd_retry(a):
    _, _, conn = library()
    q = "DELETE FROM stages WHERE status='failed'"
    args = []
    ids = folder_ids_arg(conn, a.folders)
    if ids:
        q += f" AND video_id IN (SELECT id FROM videos WHERE folder_id IN ({','.join('?' * len(ids))}))"
        args = ids
    n = conn.execute(q, args).rowcount
    conn.commit()
    print(f"{n} failed stages reset. Run `toktidy index ...` to try them again.")


def _searcher():
    settings, paths, conn = library()
    from .search import Searcher
    return settings, paths, conn, Searcher(conn, paths, settings)


def _finish_search(a, paths, hits, title, subtitle, show_score=True):
    print_hits(hits, show_score)
    if a.html:
        from . import report
        out = report.write_results(paths, title, subtitle, hits, show_score)
        print(f"\nOpened {out}")
        open_file(out)


def cmd_search(a):
    settings, paths, conn, s = _searcher()
    key = model_arg(settings, a.model)
    t = time.time()
    hits = s.text(key, a.query, folder_ids_arg(conn, a.folders), a.exclude or (), a.limit, a.hide_used)
    label = settings["models"][key].get("label", key)
    print(f"{label}: {len(hits)} results in {time.time() - t:.2f}s\n")
    _finish_search(a, paths, hits, a.query, f"{label} · text search")


def cmd_search_image(a):
    settings, paths, conn, s = _searcher()
    key = model_arg(settings, a.model)
    hits = s.image(key, a.image, folder_ids_arg(conn, a.folders), a.exclude or (), a.limit, a.hide_used)
    label = settings["models"][key].get("label", key)
    _finish_search(a, paths, hits, f"Like {Path(a.image).name}", f"{label} · image search")


def cmd_similar(a):
    settings, paths, conn, s = _searcher()
    key = model_arg(settings, a.model)
    ids = [resolve_clip(conn, c) for c in a.clips]
    hits = s.similar(key, ids, folder_ids_arg(conn, a.folders), a.limit, a.hide_used)
    names = ", ".join(db.video_path(conn, i).name for i in ids)
    label = settings["models"][key].get("label", key)
    _finish_search(a, paths, hits, f"Similar to {names}", f"{label} · find similar")


def cmd_search_audio(a):
    settings, paths, conn = library()
    from .search import audio_search
    hits = audio_search(conn, a.query, folder_ids_arg(conn, a.folders), a.limit, a.hide_used)
    _finish_search(a, paths, hits, f"Said: “{a.query}”", "audio search", show_score=False)


def cmd_search_color(a):
    settings, paths, conn = library()
    from .search import color_search, parse_color
    rgb = parse_color(a.color)
    hits = color_search(conn, rgb, folder_ids_arg(conn, a.folders), a.limit, a.hide_used)
    _finish_search(a, paths, hits, f"Colour {a.color}", "palette match")


def cmd_compare(a):
    settings, paths, conn, s = _searcher()
    keys = a.models or list(settings["models"])
    queries = list(a.queries)
    if a.queries_file:
        queries += [q.strip() for q in Path(a.queries_file).read_text(encoding="utf-8").splitlines() if q.strip()]
    if not queries:
        raise ConfigError("Give at least one search, e.g.  toktidy compare \"red dress\" \"at the beach\"")
    fids = folder_ids_arg(conn, a.folders)
    sections = []
    for q in queries:
        cols = []
        for k in keys:
            t = time.time()
            hits = s.text(k, q, fids, (), a.top, False)
            cols.append((settings["models"][k].get("label", k), hits, time.time() - t))
        sections.append((q, cols))
        print(f"  searched “{q}”")
    from . import report
    counts = ", ".join(f"{settings['models'][k].get('label', k)}: {len(s.index(k)):,} clips" for k in keys)
    out = report.write_compare(
        paths, f"{counts}. Scores are only comparable within one model.", sections)
    print(f"\nOpened {out}")
    open_file(out)


def cmd_relink_folder(a):
    settings, paths, conn = library()
    f = db.resolve_folder(conn, a.folder)
    res = scanner.relink_folder(conn, paths, settings, f, Path(a.new_path))
    print("Relinked. " + res.summary())
    if res.changed or res.new:
        print("New or changed clips will be indexed the next time you run `toktidy index`.")


def cmd_cache_info(a):
    settings, paths, conn = library()
    print(f"Settings: {settings_path()}\n")
    for part in cache.ALL_PARTS:
        p = paths.part(part)
        if part == "database":
            size = sum(Path(str(p) + s).stat().st_size for s in ("", "-wal") if Path(str(p) + s).exists())
            count = 1
        else:
            print(f"  measuring {part} ...", end="\r")
            count, size = cache.dir_size(p)
        print(f"  {part:10} {size / 1e9:8.2f} GB  {count:>10,} files   {p}")
    for f in sorted(paths.embeddings.glob("*.db")):
        from .embstore import EmbeddingStore
        st = EmbeddingStore(f)
        print(f"             {f.stem}: {st.count():,} clips embedded ({st.get_meta('fingerprint')})")
        st.close()
    print(f"  temp       {paths.temp}")


def cmd_move_cache(a):
    dest = cache.move_cache(a.part, Path(a.destination))
    print(f"Done. {a.part} now lives at {dest}")


def cmd_relink_cache(a):
    dest = cache.relink_cache(a.part, Path(a.location))
    print(f"{a.part} relinked to {dest}")


def cmd_cleanup(a):
    settings, paths, conn = library()
    missing = conn.execute("SELECT COUNT(*) FROM videos WHERE present=0").fetchone()[0]
    print(f"{missing:,} clips are marked missing (deleted, or moved to a folder that hasn't been scanned).")
    print("Tip: run `toktidy scan` first so moved clips are recognised instead of deleted.")
    if missing and (a.yes or input("Delete their cache and forget them? [y/N] ").strip().lower() == "y"):
        n = scanner.purge_missing(conn, paths)
        print(f"Removed {n:,} clips.")
    # orphaned cache files (no matching clip)
    known = {r[0] for r in conn.execute("SELECT id FROM videos")}
    orphans = 0
    for shard in paths.previews.iterdir() if paths.previews.exists() else []:
        if shard.is_dir():
            for f in shard.glob("*.mp4"):
                stem = f.stem.split(".")[0]
                if f.name.startswith(".") or not stem.isdigit() or int(stem) not in known:
                    f.unlink(missing_ok=True)
                    orphans += 1
    import shutil
    for shard in paths.frames.iterdir() if paths.frames.exists() else []:
        if shard.is_dir():
            for d in shard.iterdir():
                name = d.name.split(".")[0]
                if d.is_dir() and (not name.isdigit() or int(name) not in known or d.name.endswith(".part")):
                    shutil.rmtree(d, ignore_errors=True)
                    orphans += 1
    from .embstore import EmbeddingStore
    for f in paths.embeddings.glob("*.db"):
        st = EmbeddingStore(f)
        stale = st.video_ids() - known
        for vid in stale:
            st.delete(vid)
        st.commit()
        st.close()
        orphans += len(stale)
    print(f"Removed {orphans:,} leftover cache items.")


def cmd_used(a):
    _, _, conn = library()
    now = time.time()
    for c in a.clips:
        vid = resolve_clip(conn, c)
        conn.execute("UPDATE videos SET used=?, used_at=? WHERE id=?",
                     (0 if a.unmark else 1, None if a.unmark else now, vid))
    conn.commit()
    print(f"{len(a.clips)} clip(s) {'unmarked' if a.unmark else 'marked as used'}.")


def cmd_reset_used(a):
    _, _, conn = library()
    if not a.folders and not a.all:
        raise ConfigError("Say which folders (names/ids) or use --all.")
    if a.all:
        n = conn.execute("UPDATE videos SET used=0, used_at=NULL WHERE used=1").rowcount
    else:
        ids = folder_ids_arg(conn, a.folders)
        n = conn.execute(
            f"UPDATE videos SET used=0, used_at=NULL WHERE used=1 AND folder_id IN ({','.join('?' * len(ids))})",
            ids).rowcount
    conn.commit()
    print(f"Cleared {n:,} used markers.")


def cmd_reset_model(a):
    settings, paths, conn = library()
    f = cache.embeddings_file(paths, a.model)
    if not a.yes and input(f"Delete all {a.model} embeddings? Frames are kept, so re-embedding is quick. [y/N] ").strip().lower() != "y":
        return
    from .embstore import EmbeddingStore
    if f.exists():
        st = EmbeddingStore(f)
        st.clear()
        st.close()
    n = conn.execute("DELETE FROM stages WHERE stage=?", (db.embed_stage(a.model),)).rowcount
    conn.commit()
    print(f"Cleared. {n:,} clips will be re-embedded next time you run `toktidy index`.")


def cmd_settings(a):
    p = settings_path()
    if not p.exists():
        save_settings(load_settings())
    elif a.fill:
        save_settings(load_settings())
        print("Added any missing options with their default values.")
    print(p)
    if a.open:
        open_file(p)


# ---------------------------------------------------------------- parser

def build_parser():
    p = argparse.ArgumentParser(prog="toktidy", description="TokTidy - index and search your TikTok library")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", metavar="command")

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    def search_opts(sp):
        sp.add_argument("--folders", "-f", nargs="+", metavar="FOLDER", help="limit to these folders (names or ids)")
        sp.add_argument("--limit", "-n", type=int, default=50, help="number of results (0 = all)")
        sp.add_argument("--hide-used", action="store_true", help="leave out clips marked as used")
        sp.add_argument("--html", action="store_true", help="also open the results as a playable page")

    sp = add("init", cmd_init, "create a new library (cache folders + database)")
    sp.add_argument("--cache", required=True, help="main cache folder, e.g. D:\\TokTidyCache")
    sp.add_argument("--frames"); sp.add_argument("--previews"); sp.add_argument("--embeddings")
    sp.add_argument("--database-dir")
    sp.add_argument("--force", action="store_true")

    sp = add("doctor", cmd_doctor, "check that everything is installed correctly")
    sp.add_argument("--whisper", action="store_true", help="also test loading Whisper")

    sp = add("add-folder", cmd_add_folder, "remember one or more TikTok folders")
    sp.add_argument("paths", nargs="+")
    sp = add("add-parent", cmd_add_parent, "remember every sub-folder of a parent folder")
    sp.add_argument("parent")
    sp = add("remove-folder", cmd_remove_folder, "forget a folder and delete its cache")
    sp.add_argument("folder"); sp.add_argument("--yes", action="store_true")
    add("folders", cmd_folders, "list remembered folders and their indexing progress")
    add("status", cmd_status, "folders plus library totals")

    sp = add("scan", cmd_scan, "check folders for new/changed/moved clips (no indexing)")
    sp.add_argument("folders", nargs="*")

    sp = add("index", cmd_index, "index new clips (safe to stop and resume)")
    sp.add_argument("folders", nargs="*", help="folder names or ids")
    sp.add_argument("--all", action="store_true", help="every remembered folder")
    sp.add_argument("--only", choices=["decode", "embed", "transcribe"], help="run just one step")
    sp.add_argument("--model", nargs="+", help="only these search models")
    sp.add_argument("--no-transcribe", action="store_true")
    sp.add_argument("--limit", type=int, help="only the first N clips per folder (for testing)")

    sp = add("failed", cmd_failed, "list clips that failed to index")
    sp.add_argument("folders", nargs="*")
    sp = add("retry", cmd_retry, "reset failed clips so the next index run tries again")
    sp.add_argument("folders", nargs="*")

    sp = add("search", cmd_search, "search by description")
    sp.add_argument("query", help='e.g. "red dress"  or  "red dress + beach"')
    sp.add_argument("--model", "-m", help="siglip2 or openclip")
    sp.add_argument("--exclude", "-x", nargs="+", metavar="TERM", help="push down clips matching these")
    search_opts(sp)

    sp = add("search-image", cmd_search_image, "find clips that look like a picture")
    sp.add_argument("image"); sp.add_argument("--model", "-m")
    sp.add_argument("--exclude", "-x", nargs="+", metavar="TERM")
    search_opts(sp)

    sp = add("similar", cmd_similar, "find clips similar to one or more clips")
    sp.add_argument("clips", nargs="+", help="clip file paths (or ids)")
    sp.add_argument("--model", "-m")
    search_opts(sp)

    sp = add("search-audio", cmd_search_audio, "find clips where a word/phrase is spoken")
    sp.add_argument("query"); search_opts(sp)

    sp = add("search-color", cmd_search_color, "find clips with a colour in their palette")
    sp.add_argument("color", help="a name like pink or a hex code like #ff3366"); search_opts(sp)

    sp = add("compare", cmd_compare, "compare SigLIP 2 and OpenCLIP side by side")
    sp.add_argument("queries", nargs="*")
    sp.add_argument("--queries-file", help="text file with one search per line")
    sp.add_argument("--folders", "-f", nargs="+")
    sp.add_argument("--models", nargs="+")
    sp.add_argument("--top", type=int, default=12)

    sp = add("relink-folder", cmd_relink_folder, "point a remembered folder to its new location")
    sp.add_argument("folder"); sp.add_argument("new_path")

    add("cache-info", cmd_cache_info, "show cache locations and sizes")
    sp = add("move-cache", cmd_move_cache, "safely move part of the cache (database/frames/previews/embeddings)")
    sp.add_argument("part", choices=cache.ALL_PARTS); sp.add_argument("destination")
    sp = add("relink-cache", cmd_relink_cache, "point to a cache part you moved yourself")
    sp.add_argument("part", choices=cache.ALL_PARTS); sp.add_argument("location")
    sp = add("cleanup", cmd_cleanup, "forget missing clips and remove leftover cache files")
    sp.add_argument("--yes", action="store_true")

    sp = add("mark-used", cmd_used, "mark clips as used (or --unmark)")
    sp.add_argument("clips", nargs="+"); sp.add_argument("--unmark", action="store_true")
    sp = add("reset-used", cmd_reset_used, "clear used markers")
    sp.add_argument("folders", nargs="*"); sp.add_argument("--all", action="store_true")

    sp = add("reset-model", cmd_reset_model, "delete one model's embeddings (e.g. after changing the model)")
    sp.add_argument("model"); sp.add_argument("--yes", action="store_true")

    sp = add("settings", cmd_settings, "show (or --open) the settings file")
    sp.add_argument("--open", action="store_true")
    sp.add_argument("--fill", action="store_true", help="write all default options into the file")

    add("help", lambda a: p.print_help(), "show this help")
    return p


def main(argv=None):
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    parser = build_parser()
    a = parser.parse_args(argv)
    if not getattr(a, "fn", None):
        parser.print_help()
        return 0
    try:
        a.fn(a)
    except (ConfigError, LookupError) as e:
        if isinstance(e, KeyError):
            raise
        print(f"\n{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1
    return 0
