"""Local HTML pages for checking results before the full app exists.

They play the 240p previews straight from the cache, so they open instantly
in Edge or Chrome. Only clips on screen play, to keep things smooth.
"""
from __future__ import annotations

import html
import time
from pathlib import Path

from . import cache

CSS = """
:root { color-scheme: dark;
  --bg:#191b21; --panel:#22252d; --line:#323743; --text:#e7e9ee; --muted:#9aa1b0;
  --accent:#2ee8e0; --used:#e0144f; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:14px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif; }
header { padding:20px 28px 12px; border-bottom:1px solid var(--line); }
h1 { margin:0 0 4px; font-size:22px; font-weight:600; }
.sub { color:var(--muted); }
section { padding:18px 28px 8px; }
h2 { font-size:17px; font-weight:600; margin:0 0 12px; }
.cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:28px; }
.col h3 { font-size:14px; font-weight:600; color:var(--accent); margin:0 0 10px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(var(--tile,170px),1fr)); gap:12px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.card.used { border-color:var(--used); }
.media { position:relative; aspect-ratio:9/16; background:#000; }
.media video { width:100%; height:100%; object-fit:contain; display:block; }
.rank { position:absolute; top:6px; left:6px; background:rgba(0,0,0,.65); padding:1px 7px;
  border-radius:10px; font-size:12px; }
.info { padding:7px 9px 9px; }
.name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:500; }
.meta { color:var(--muted); font-size:12px; }
.score { color:var(--accent); font-variant-numeric:tabular-nums; }
.snip { font-size:12px; margin-top:4px; color:var(--text); }
.row { display:flex; gap:8px; margin-top:6px; flex-wrap:wrap; }
.row a, .row button { color:var(--text); background:#2d313b; border:1px solid var(--line);
  border-radius:5px; padding:2px 8px; font:inherit; font-size:12px; text-decoration:none; cursor:pointer; }
.row a:hover, .row button:hover { border-color:var(--accent); }
.empty { color:var(--muted); padding:20px 0; }
.toolbar { margin-top:10px; color:var(--muted); }
.toolbar input { vertical-align:middle; }
"""

JS = """
const io = new IntersectionObserver(entries => {
  for (const e of entries) {
    const v = e.target;
    if (e.isIntersecting) { if (!v.src) v.src = v.dataset.src; v.play().catch(()=>{}); }
    else { v.pause(); }
  }
}, { rootMargin: '200px' });
document.querySelectorAll('video[data-src]').forEach(v => io.observe(v));
document.querySelectorAll('button[data-seek]').forEach(b => b.addEventListener('click', () => {
  const v = b.closest('.card').querySelector('video');
  v.currentTime = parseFloat(b.dataset.seek); v.play().catch(()=>{});
}));
const size = document.getElementById('size');
if (size) size.addEventListener('input', () =>
  document.documentElement.style.setProperty('--tile', size.value + 'px'));
"""


def _fmt_time(t):
    if t is None:
        return ""
    t = float(t)
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _fmt_size(n):
    if not n:
        return ""
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def _card(paths: cache.Paths, hit, rank: int, show_score=True) -> str:
    r = hit.row
    vid = hit.video_id
    prev = cache.preview_path(paths, vid)
    src = prev.as_uri() if prev.exists() else ""
    poster = ""
    if hit.frame_idx is not None:
        fp = cache.frame_path(paths, vid, hit.frame_idx)
        if fp.exists():
            poster = f' poster="{fp.as_uri()}"'
    elif cache.frame_path(paths, vid, 0).exists():
        poster = f' poster="{cache.frame_path(paths, vid, 0).as_uri()}"'
    meta = " · ".join(x for x in [
        _fmt_time(r.get("duration")),
        f"{r['width']}×{r['height']}" if r.get("width") else "",
        f"{r['fps']:g} fps" if r.get("fps") else "",
        _fmt_size(r.get("size")),
    ] if x)
    score = f'<span class="score">{hit.score:.3f}</span> · ' if show_score else ""
    seek = ""
    if hit.time is not None:
        seek = f'<button data-seek="{hit.time}">match at {_fmt_time(hit.time)}</button>'
    snip = f'<div class="snip">“{html.escape(hit.snippet)}”</div>' if hit.snippet else ""
    original = (Path(r["folder_path"]) / r["filename"]).as_uri()
    return f"""
<div class="card{' used' if r.get('used') else ''}">
  <div class="media"><span class="rank">{rank}</span>
    <video data-src="{src}"{poster} muted loop playsinline preload="none"></video></div>
  <div class="info">
    <div class="name" title="{html.escape(r['filename'])}">{html.escape(r['filename'])}</div>
    <div class="meta">{score}{html.escape(r.get('folder_name', ''))}</div>
    <div class="meta">{meta}</div>{snip}
    <div class="row">{seek}<a href="{original}" target="_blank">Open original</a></div>
  </div>
</div>"""


def _grid(paths, hits, show_score=True) -> str:
    if not hits:
        return '<div class="empty">No matches.</div>'
    return '<div class="grid">' + "".join(
        _card(paths, h, i + 1, show_score) for i, h in enumerate(hits)) + "</div>"


def _page(title, subtitle, body) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} – TokTidy</title><style>{CSS}</style></head><body>
<header><h1>{html.escape(title)}</h1><div class="sub">{html.escape(subtitle)}</div>
<div class="toolbar"><label>Tile size <input id="size" type="range" min="110" max="320" value="170"></label></div>
</header>{body}<script>{JS}</script></body></html>"""


def reports_dir(paths: cache.Paths) -> Path:
    d = paths.database.parent / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_results(paths, title, subtitle, hits, show_score=True) -> Path:
    body = f"<section>{_grid(paths, hits, show_score)}</section>"
    out = reports_dir(paths) / f"results_{time.strftime('%Y%m%d_%H%M%S')}.html"
    out.write_text(_page(title, subtitle, body), encoding="utf-8")
    return out


def write_compare(paths, subtitle, sections) -> Path:
    """sections: list of (query, [(model_label, hits, seconds)])"""
    body = ""
    for query, columns in sections:
        cols = "".join(
            f'<div class="col"><h3>{html.escape(label)} <span class="sub">({secs:.2f}s)</span></h3>'
            f"{_grid(paths, hits)}</div>"
            for label, hits, secs in columns
        )
        body += f'<section><h2>“{html.escape(query)}”</h2><div class="cols">{cols}</div></section>'
    out = reports_dir(paths) / f"compare_{time.strftime('%Y%m%d_%H%M%S')}.html"
    out.write_text(_page("Model comparison", subtitle, body), encoding="utf-8")
    return out
