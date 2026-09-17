/* TokTidy - app page logic.
 * Talks to the Python engine over HTTP and to Electron through window.toktidy.
 */
'use strict';

// ====================================================================== helpers
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtNum = (n) => (n ?? 0).toLocaleString();
const fmtTime = (t) => (t == null ? '' : `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`);
const fmtSize = (b) => (!b ? '' : b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} KB`);
const fmtDuration = (s) => {
  if (s == null || !isFinite(s)) return '';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
};
const fileUrl = (p) => `file:///${encodeURI(p.replace(/\\/g, '/').replace(/^\/+/, '')).replace(/#/g, '%23').replace(/\?/g, '%3F')}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const show = (el, on = true) => (typeof el === 'string' ? $(el) : el).classList.toggle('hidden', !on);
const PREMIERE_RISKY = new Set(['webm', 'mkv', 'flv', 'ts', 'm2ts', 'mts', '3gp', 'wmv']);
const GAP = 10;

const S = {
  api: null,
  state: null,
  settings: null,
  folders: [],
  checked: new Set(),       // ticked folder ids (empty = all folders)
  folderFilter: '',
  mode: 'video',            // search bar: video | audio
  image: null,              // { path } or { data } for picture search
  view: null,               // current result list from the engine
  spec: { mode: 'browse' }, // what produced the current view
  page: 0,
  items: [],
  byId: new Map(),
  sel: new Set(),
  lastIdx: null,
  seed: Math.floor(Math.random() * 1e9),
  job: null,
  jobWasRunning: false,
  trio: [null, null, null],
  lastDrag: null,
};

// ====================================================================== engine calls
async function api(path, body, method) {
  const opt = { method: method || (body !== undefined ? 'POST' : 'GET'), headers: { 'x-toktidy-token': S.api.token } };
  if (body !== undefined) {
    opt.headers['content-type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  let res;
  for (let attempt = 0; ; attempt++) {
    try {
      res = await fetch(S.api.url + path, opt);
      break;
    } catch (e) {
      if (attempt >= 3) throw new Error('Lost connection to the TokTidy engine.');
      await sleep(400 * (attempt + 1));
    }
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* not json */ }
  if (!res.ok) {
    const err = new Error((data && data.detail) || text || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ====================================================================== small UI pieces
function toast(msg, kind = '', ms = 4500) {
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $('toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'error' ? Math.max(ms, 9000) : ms);
  return t;
}
const fail = (e) => toast(e.message || String(e), 'error');

function confirmBox(title, text, okLabel = 'OK', danger = false) {
  return new Promise((resolve) => {
    $('dlgTitle').textContent = title;
    $('dlgText').textContent = text;
    $('dlgOk').textContent = okLabel;
    $('dlgOk').classList.toggle('danger', danger);
    show('dialog');
    const done = (v) => {
      show('dialog', false);
      $('dlgOk').onclick = $('dlgCancel').onclick = null;
      resolve(v);
    };
    $('dlgOk').onclick = () => done(true);
    $('dlgCancel').onclick = () => done(false);
  });
}

function openMenu(x, y, entries) {
  const m = $('menu');
  m.innerHTML = '';
  for (const e of entries) {
    if (e === '-') { m.appendChild(document.createElement('hr')); continue; }
    const b = document.createElement('button');
    b.textContent = e.label;
    b.disabled = !!e.disabled;
    if (e.danger) b.classList.add('danger');
    b.onclick = () => { closeMenu(); e.action(); };
    m.appendChild(b);
  }
  show(m);
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.min(x, window.innerWidth - r.width - 6)}px`;
  m.style.top = `${Math.min(y, window.innerHeight - r.height - 6)}px`;
}
function closeMenu() { show('menu', false); }

function screen(name) {
  for (const id of ['boot', 'fatal', 'welcome', 'cacheProblem']) show(id, id === name);
  show('main', name === 'main');
}

// ====================================================================== start-up
async function boot() {
  screen('boot');
  $('bootMsg').textContent = 'Starting the engine…';
  const info = await window.toktidy.backend();
  if (!info.ok) return showFatal(info.error, info.log);
  S.api = info;
  await route();
}

async function route(retried = false) {
  try {
    S.state = await api('/api/state');
    S.settings = await api('/api/settings');
  } catch (e) {
    if (!retried) {
      // the engine may have stopped; start a fresh one once before giving up
      $('bootMsg').textContent = 'Restarting the engine…';
      const info = await window.toktidy.restartBackend();
      if (info.ok) { S.api = info; return route(true); }
    }
    return showFatal(e.message, await window.toktidy.backendLog());
  }
  if (S.state.ready) return enterMain();
  if (S.settings.paths && S.settings.paths.database) {
    // hide command-line hints; the buttons below do the same thing
    $('cpMsg').textContent = cacheMessage(S.state.error);
    return screen('cacheProblem');
  }
  screen('welcome');
  wizardStep(1);
}

// The engine's messages include command-line hints; the app's buttons do the same job.
function cacheMessage(err) {
  return (err || '').split('\n')
    .filter((l) => !/^\s*toktidy /.test(l) && !/^If you moved it/.test(l)).join('\n');
}

function showFatal(msg, log) {
  screen('fatal');
  $('fatalMsg').textContent = msg || 'Unknown error';
  $('fatalLog').textContent = (log || []).join('\n');
}

$('fatalRetry').onclick = async () => {
  screen('boot');
  $('bootMsg').textContent = 'Restarting the engine…';
  const info = await window.toktidy.restartBackend();
  if (!info.ok) return showFatal(info.error, info.log);
  S.api = info;
  await route();
};
$('fatalCopy').onclick = async () => {
  const appInfo = await window.toktidy.appInfo();
  await window.toktidy.copyText(`TokTidy start-up error\n${JSON.stringify(appInfo, null, 1)}\n\n${$('fatalMsg').textContent}\n\n${$('fatalLog').textContent}`);
  toast('Copied. Paste it into the chat.', 'ok');
};
$('fatalLogs').onclick = () => window.toktidy.openLogs();

window.toktidy.onBackendExit((d) => {
  showFatal(`The TokTidy engine stopped unexpectedly (exit code ${d.code}). Press “Try again” to restart it; your library is safe.`, d.log);
});

// ====================================================================== first-run wizard
let wizardCache = null;
function wizardStep(n) {
  document.querySelectorAll('.wstep').forEach((el) => show(el, el.dataset.step === String(n)));
  document.querySelectorAll('.steps li').forEach((li) => {
    const s = Number(li.dataset.step);
    li.classList.toggle('current', s === n);
    li.classList.toggle('done', s < n);
  });
}

$('wPickCache').onclick = async () => {
  const [dir] = await window.toktidy.pickFolder({ title: 'Where should the TokTidy cache go?' });
  if (!dir) return;
  const base = dir.replace(/[\\/]+$/, '');
  // Use a folder named like "TokTidy Cache" as-is; otherwise make one inside the chosen folder.
  wizardCache = /tok[\s_-]*tidy[\s_-]*cache$/i.test(base) ? base : `${base}\\TokTidy Cache`;
  $('wCachePath').textContent = wizardCache;
  $('wCachePath').classList.remove('muted');
  $('wCreate').disabled = false;
};

$('wCreate').onclick = async () => {
  $('wCreate').disabled = true;
  try {
    S.state = await api('/api/setup/init', { cache: wizardCache });
    S.settings = await api('/api/settings');
    wizardStep(2);
  } catch (e) {
    fail(e);
    $('wCreate').disabled = false;
  }
};

$('wExisting').onclick = async () => {
  const file = await window.toktidy.pickFile({ title: 'Find your library.db', filters: [{ name: 'TokTidy library', extensions: ['db'] }] });
  if (!file) return;
  await relinkPart('database', file);
};

async function wizardAdd(opts) {
  try {
    if (opts.parent) {
      const [dir] = await window.toktidy.pickFolder({ title: 'Choose the folder that contains your TikTok folders' });
      if (!dir) return;
      await api('/api/folders/add', { parent: dir });
    } else {
      const dirs = await window.toktidy.pickFolder({ title: 'Choose TikTok folders', multi: true });
      if (!dirs.length) return;
      await api('/api/folders/add', { paths: dirs });
    }
    const folders = await api('/api/folders');
    $('wFolderList').innerHTML = folders.map((f) => `<li>${esc(f.name)} <span class="muted">— ${fmtNum(f.clips)} clips</span></li>`).join('');
    $('wToStep3').disabled = folders.length === 0;
  } catch (e) { fail(e); }
}
$('wAddFolders').onclick = () => wizardAdd({});
$('wAddParent').onclick = () => wizardAdd({ parent: true });
$('wToStep3').onclick = () => wizardStep(3);
$('wSkip').onclick = () => route();
$('wIndexAll').onclick = async () => { await route(); startIndex({ all: true }); };
$('wIndexTest').onclick = async () => { await route(); startIndex({ all: true, limit: 50 }); };

// ====================================================================== cache problem screen
async function relinkPart(part, path) {
  try {
    const st = await api('/api/cache/relink', { part, path });
    toast(`${part} relinked.`, 'ok');
    if (st.ready) await route();
    else $('cpMsg').textContent = cacheMessage(st.error);
  } catch (e) { fail(e); }
}
document.querySelectorAll('[data-relink]').forEach((b) => {
  b.onclick = async () => {
    const part = b.dataset.relink;
    let p;
    if (part === 'database') {
      p = await window.toktidy.pickFile({ title: 'Find library.db', filters: [{ name: 'TokTidy library', extensions: ['db'] }] });
    } else {
      [p] = await window.toktidy.pickFolder({ title: `Find the ${part} folder` });
    }
    if (p) await relinkPart(part, p);
  };
});
$('cpRetry').onclick = () => route();
$('cpNew').onclick = async () => {
  const ok = await confirmBox('Start a new library?',
    'Your old cache stays on disk but will no longer be used. Your videos are never touched.', 'Start new', true);
  if (!ok) return;
  screen('welcome');
  wizardStep(1);
  $('wCreate').onclick = async () => {
    try {
      S.state = await api('/api/setup/init', { cache: wizardCache, force: true });
      wizardStep(2);
    } catch (e) { fail(e); }
  };
};

// ====================================================================== main screen
let mainStarted = false;
async function enterMain() {
  screen('main');
  applyPrefs();
  fillModelSelect();
  await loadFolders();
  if (!mainStarted) {
    mainStarted = true;
    Grid.init();
    pollIndex();
  }
  await runView({ mode: 'browse' });
}

function prefs() { return S.settings.app; }

function applyPrefs() {
  const p = prefs();
  $('sortSel').value = p.sort || 'name';
  $('sortDir').textContent = p.sort_desc ? '↓' : '↑';
  $('hideUsed').checked = !!p.hide_used;
  $('showDetails').checked = !!p.show_details;
  document.body.classList.toggle('no-details', !p.show_details);
  $('tileSize').value = p.tile_width || 170;
  $('pageSize').value = p.page_size || 500;
  $('pageSizeOut').textContent = p.page_size || 500;
  syncSortControls();
}

const BOTH = 'both';
function modelLabel(key) {
  if (key === BOTH) return 'Both models';
  const m = S.state.models[key];
  return m ? m.label : '';
}

function fillModelSelect() {
  const models = S.state.models;
  const on = Object.entries(models).filter(([, m]) => m.enabled);
  let opts = on.map(([k, m]) => `<option value="${esc(k)}">${esc(m.label)}</option>`).join('');
  if (on.length > 1) opts += `<option value="${BOTH}">Both models</option>`;
  const current = $('modelSel').value;
  $('modelSel').innerHTML = opts || '<option value="">No model enabled</option>';
  const def = S.settings.default_model;
  const valid = (k) => (k === BOTH ? on.length > 1 : !!(models[k] && models[k].enabled));
  if (valid(def)) $('modelSel').value = def;
  else if (valid(current)) $('modelSel').value = current;
}

const savePrefs = (() => {
  let timer = null;
  let pending = {};
  return (patch) => {
    Object.assign(pending, patch);
    Object.assign(S.settings.app, patch);
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const body = { app: pending };
      pending = {};
      try { S.settings = await api('/api/settings', body); } catch (e) { fail(e); }
    }, 500);
  };
})();

// ====================================================================== folders
async function loadFolders() {
  try {
    S.folders = await api('/api/folders');
  } catch (e) { return fail(e); }
  const ids = new Set(S.folders.map((f) => f.id));
  for (const id of [...S.checked]) if (!ids.has(id)) S.checked.delete(id);
  renderFolders();
}

function renderFolders() {
  const q = S.folderFilter.toLowerCase();
  const list = S.folders.filter((f) => !q || f.name.toLowerCase().includes(q));
  $('folderList').innerHTML = list.map((f) => {
    const pct = f.clips ? Math.round((f.indexed / f.clips) * 100) : 0;
    const done = f.clips > 0 && f.indexed === f.clips;
    const flags = [];
    if (!f.online) flags.push('<span class="bad">offline</span>');
    if (f.failed) flags.push(`<span class="bad">${fmtNum(f.failed)} failed</span>`);
    if (!done && f.clips) flags.push(`${pct}% indexed`);
    if (f.searchable < f.clips && f.searchable > 0 && !done) flags.push(`${fmtNum(f.searchable)} searchable`);
    return `<li class="folder${S.checked.has(f.id) ? ' checked' : ''}${f.online ? '' : ' offline'}" data-id="${f.id}" title="${esc(f.path)}">
      <input type="checkbox" ${S.checked.has(f.id) ? 'checked' : ''} tabindex="-1">
      <span>${esc(f.name)}</span><em>${fmtNum(f.clips)}</em>
      <div class="mini${done ? '' : ' partial'}"><div style="width:${pct}%"></div></div>
      ${flags.length ? `<div class="flags">${flags.join(' · ')}</div>` : ''}
    </li>`;
  }).join('') || `<li class="muted small" style="padding:8px">${S.folders.length ? 'No folders match.' : 'No folders yet. Click + Add.'}</li>`;
  $('allFolders').checked = S.checked.size === 0;
  const total = S.folders.reduce((a, f) => a + f.clips, 0);
  $('allCount').textContent = fmtNum(total);
  const n = S.checked.size;
  $('ixScope').textContent = n ? `In ${n} ticked folder${n > 1 ? 's' : ''}` : 'In all folders';
  const failed = S.folders.reduce((a, f) => a + f.failed, 0);
  $('ixFailed').textContent = `${fmtNum(failed)} failed clip${failed === 1 ? '' : 's'}`;
  show('ixFailed', failed > 0);
}

$('folderFilter').oninput = (e) => { S.folderFilter = e.target.value; renderFolders(); };
$('allFolders').onchange = () => {
  S.checked.clear();
  renderFolders();
  rerun();
};
$('folderList').onclick = (e) => {
  const li = e.target.closest('.folder');
  if (!li) return;
  const id = Number(li.dataset.id);
  if (e.ctrlKey || e.target.type === 'checkbox') {
    if (S.checked.has(id)) S.checked.delete(id); else S.checked.add(id);
  } else {
    // plain click = just this folder (click again to go back to all)
    if (S.checked.size === 1 && S.checked.has(id)) S.checked.clear();
    else { S.checked.clear(); S.checked.add(id); }
  }
  renderFolders();
  rerun();
};
$('folderList').oncontextmenu = (e) => {
  const li = e.target.closest('.folder');
  if (!li) return;
  e.preventDefault();
  const f = S.folders.find((x) => x.id === Number(li.dataset.id));
  if (!f) return;
  const busy = S.job && S.job.running;
  openMenu(e.clientX, e.clientY, [
    { label: 'Index this folder', disabled: busy, action: () => startIndex({ ids: [f.id] }) },
    { label: 'Test run (first 50 clips)', disabled: busy, action: () => startIndex({ ids: [f.id], limit: 50 }) },
    { label: 'Show in Explorer', action: () => window.toktidy.openPath(f.path) },
    '-',
    { label: 'Folder moved or renamed…', disabled: busy, action: () => relinkFolder(f) },
    { label: 'Clear used markers', action: () => resetUsed([f.id]) },
    '-',
    { label: 'Remove from library…', danger: true, disabled: busy, action: () => removeFolder(f) },
  ]);
};

async function addFolders(parent) {
  try {
    if (parent) {
      const [dir] = await window.toktidy.pickFolder({ title: 'Choose the folder that contains your TikTok folders' });
      if (!dir) return;
      const r = await api('/api/folders/add', { parent: dir });
      toast(`Added ${r.added.length} folder(s)${r.skipped ? ` (skipped ${r.skipped} without videos or already added)` : ''}.`, 'ok');
    } else {
      const dirs = await window.toktidy.pickFolder({ title: 'Choose TikTok folders', multi: true });
      if (!dirs.length) return;
      const r = await api('/api/folders/add', { paths: dirs });
      toast(`Added ${r.added.length} folder(s).`, 'ok');
    }
    await loadFolders();
    rerun();
  } catch (e) { fail(e); }
}
$('addFolderBtn').onclick = (e) => {
  const r = e.target.getBoundingClientRect();
  openMenu(r.left, r.bottom + 4, [
    { label: 'Add folders…', action: () => addFolders(false) },
    { label: 'Add every folder inside…', action: () => addFolders(true) },
  ]);
};

async function relinkFolder(f) {
  const [dir] = await window.toktidy.pickFolder({ title: `Where is "${f.name}" now?` });
  if (!dir) return;
  try {
    const r = await api(`/api/folders/${f.id}/relink`, { path: dir });
    toast(`Relinked. ${r.summary}`, 'ok');
    await loadFolders();
    rerun();
  } catch (e) { fail(e); }
}

async function removeFolder(f) {
  const ok = await confirmBox(`Remove "${f.name}"?`,
    `TokTidy will forget this folder and delete its previews and search data (${fmtNum(f.clips)} clips). Your videos are not touched.`,
    'Remove', true);
  if (!ok) return;
  try {
    await api(`/api/folders/${f.id}/remove`, {});
    S.checked.delete(f.id);
    await loadFolders();
    rerun();
  } catch (e) { fail(e); }
}

async function resetUsed(folderIds) {
  const what = folderIds ? `${folderIds.length} folder(s)` : 'the whole library';
  const ok = await confirmBox('Clear used markers?', `This clears the used markers in ${what}.`, 'Clear');
  if (!ok) return;
  try {
    const r = await api('/api/used/reset', folderIds ? { folder_ids: folderIds } : {});
    toast(`Cleared ${fmtNum(r.cleared)} markers.`, 'ok');
    rerun({ keepPage: true });
  } catch (e) { fail(e); }
}

// ====================================================================== indexing panel
async function startIndex({ ids, all, limit } = {}) {
  const folderIds = all ? null : (ids || (S.checked.size ? [...S.checked] : null));
  try {
    await api('/api/index/start', { folder_ids: folderIds, limit: limit || null });
    toast(limit ? `Test run started (first ${limit} clips per folder).` : 'Indexing started. You can keep working.', 'ok');
    pollIndex(true);
  } catch (e) { fail(e); }
}
$('ixStart').onclick = () => startIndex({});
$('ixTest').onclick = () => startIndex({ limit: 50 });
$('ixStop').onclick = async () => {
  $('ixStop').disabled = true;
  $('ixStop').textContent = 'Stopping after the current clips…';
  try { await api('/api/index/stop', {}); } catch (e) { fail(e); }
};
$('ixLogToggle').onclick = () => {
  const on = $('ixLog').classList.contains('hidden');
  show('ixLog', on);
  $('ixLogToggle').textContent = on ? 'Hide log' : 'Show log';
};
$('ixFailed').onclick = () => openSettings('library');

let pollTimer = null;
let pollTick = 0;
let pollGen = 0;
async function pollIndex(now) {
  clearTimeout(pollTimer);
  const gen = ++pollGen;
  if (now) await sleep(300);
  let st = null;
  try { st = await api('/api/index/status'); } catch { /* engine busy or gone */ }
  if (gen !== pollGen) return;
  if (st) {
    S.job = st.job;
    renderIndex(st);
    const running = !!(st.job && st.job.running);
    pollTick++;
    if (running && pollTick % 4 === 0) loadFolders();
    if (S.jobWasRunning && !running) {
      await loadFolders();
      const j = st.job;
      if (j.error) toast(`Indexing stopped with an error:\n${j.error}`, 'error');
      else toast(j.result === 'stopped' ? 'Indexing paused. Press "Index new clips" to continue.' : 'Indexing finished.', 'ok');
      if (S.spec.mode === 'browse') rerun({ keepPage: true });
    }
    S.jobWasRunning = running;
  }
  if (gen === pollGen) pollTimer = setTimeout(() => pollIndex(), S.job && S.job.running ? 1500 : 6000);
}

function renderIndex(st) {
  const j = st.job;
  const running = !!(j && j.running);
  show('ixIdle', !running);
  show('ixRun', running);
  if (running) {
    $('ixFolder').textContent = j.folder_total ? `Folder ${j.folder_n} of ${j.folder_total}: ${j.folder}` : 'Starting…';
    $('ixStep').textContent = j.step || '';
    const pct = j.total ? (j.done / j.total) * 100 : 0;
    $('ixBar').style.width = `${pct}%`;
    const bits = [];
    if (j.total) bits.push(`${fmtNum(j.done)} / ${fmtNum(j.total)}`);
    if (j.rate) bits.push(j.rate >= 1 ? `${j.rate.toFixed(1)} clips/s` : `${(1 / j.rate).toFixed(1)} s/clip`);
    if (j.eta) bits.push(`~${fmtDuration(j.eta)} left in this step`);
    if (j.step_failed) bits.push(`${j.step_failed} failed`);
    $('ixNums').textContent = bits.join(' · ');
    $('ixStop').disabled = !!j.stopping;
    $('ixStop').textContent = j.stopping ? 'Stopping after the current clips…' : 'Stop';
  } else {
    $('ixStop').disabled = false;
    $('ixStop').textContent = 'Stop';
    if (j && j.finished) {
      const when = new Date(j.finished * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      $('ixLast').textContent = j.error ? `Last run failed at ${when}.` :
        j.result === 'stopped' ? `Paused at ${when}. Press the button to continue.` : `Last run finished at ${when}.`;
    }
  }
  if (!$('ixLog').classList.contains('hidden')) {
    const atBottom = $('ixLog').scrollTop + $('ixLog').clientHeight >= $('ixLog').scrollHeight - 10;
    $('ixLog').textContent = (st.log || []).join('\n');
    if (atBottom) $('ixLog').scrollTop = $('ixLog').scrollHeight;
  }
}

// ====================================================================== search & views
function setMode(mode) {
  S.mode = mode;
  $('modeVideo').classList.toggle('on', mode === 'video');
  $('modeAudio').classList.toggle('on', mode === 'audio');
  document.body.classList.toggle('audio-mode', mode === 'audio');
  $('q').placeholder = mode === 'audio'
    ? 'Words someone says, e.g. outfit check'
    : "Describe what you're looking for, e.g. red dress + beach";
  $('q').focus();
}
$('modeVideo').onclick = () => setMode('video');
$('modeAudio').onclick = () => setMode('audio');

function runSearch() {
  const q = $('q').value.trim();
  if (S.mode === 'audio') {
    if (!q) return toast('Type a word or phrase to search for.');
    return runView({ mode: 'audio', query: q });
  }
  const exclude = $('qNot').value.split(',').map((s) => s.trim()).filter(Boolean);
  if (S.image) {
    return runView({ mode: 'image', image_path: S.image.path, image_data: S.image.data, exclude, label: S.image.name });
  }
  if (!q) return runView({ mode: 'browse' });
  return runView({ mode: 'text', query: q, exclude });
}
$('searchBtn').onclick = runSearch;
$('q').onkeydown = (e) => { if (e.key === 'Enter') runSearch(); };
$('qNot').onkeydown = (e) => { if (e.key === 'Enter') runSearch(); };
$('modelSel').onchange = () => { if (['text', 'image', 'similar'].includes(S.spec.mode)) rerun(); };

// ---------------------------------------------------------------------- colour picker
// In-app picker (the Windows colour dialog can't have an Apply button).
// Starts on white every time the app opens; Apply searches and keeps it open.
const ColorPicker = (() => {
  const PRESETS = [
    ['#ffffff', 'White'], ['#111111', 'Black'], ['#8a8a8a', 'Grey'], ['#d9c3a0', 'Beige'],
    ['#7a4a2a', 'Brown'], ['#d7262e', 'Red'], ['#f07b1f', 'Orange'], ['#f2d027', 'Yellow'],
    ['#3f9e45', 'Green'], ['#1fb5c9', 'Teal'], ['#2f5fd0', 'Blue'], ['#1c2a55', 'Navy'],
    ['#7b3fb8', 'Purple'], ['#f06aa6', 'Pink'],
  ];
  let h = 0, sat = 0, val = 1;   // white

  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  function toHex() {
    const f = (n) => {
      const k = (n + h / 60) % 6;
      return val - val * sat * Math.max(0, Math.min(k, 4 - k, 1));
    };
    return '#' + [f(5), f(3), f(1)].map((c) => Math.round(c * 255).toString(16).padStart(2, '0')).join('');
  }
  function fromHex(hex) {
    const m = /^#?([0-9a-f]{6}|[0-9a-f]{3})$/i.exec(hex.trim());
    if (!m) return false;
    let t = m[1];
    if (t.length === 3) t = t.split('').map((c) => c + c).join('');
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(t.slice(i, i + 2), 16) / 255);
    const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b);
    let hue = h;   // keep hue for greys so the slider doesn't jump
    if (d) {
      hue = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
      hue = (hue * 60 + 360) % 360;
    }
    h = hue; sat = mx ? d / mx : 0; val = mx;
    return true;
  }
  function render(skipInput) {
    const hex = toHex();
    $('cpSV').style.backgroundColor = `hsl(${h}, 100%, 50%)`;
    $('cpSVKnob').style.left = `${sat * 100}%`;
    $('cpSVKnob').style.top = `${(1 - val) * 100}%`;
    $('cpHueKnob').style.left = `${(h / 360) * 100}%`;
    $('cpPreview').style.background = hex;
    $('colorDot').style.background = hex;
    if (!skipInput) { $('cpHex').value = hex.toUpperCase(); $('cpHex').classList.remove('bad'); }
    $('cpSwatches').querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.hex === hex));
  }
  function drag(el, onMove) {
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      el.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const r = el.getBoundingClientRect();
        onMove(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1));
        render();
      };
      move(e);
      el.onpointermove = move;
      el.onpointerup = el.onpointercancel = () => { el.onpointermove = null; };
    });
  }
  function apply() {
    if (!fromHex($('cpHex').value)) { $('cpHex').classList.add('bad'); return; }
    render();
    runView({ mode: 'color', color: toHex() });
  }
  function isOpen() { return !$('colorPop').classList.contains('hidden'); }
  function open() {
    show('colorPop', true);
    $('colorBtn').classList.add('open');
    $('colorBtn').setAttribute('aria-expanded', 'true');
    render();
    $('cpApply').focus();
  }
  function close(returnFocus) {
    if (!isOpen()) return;
    show('colorPop', false);
    $('colorBtn').classList.remove('open');
    $('colorBtn').setAttribute('aria-expanded', 'false');
    if (returnFocus) $('colorBtn').focus();
  }

  $('cpSwatches').innerHTML = PRESETS.map(([hex, name]) =>
    `<button data-hex="${hex}" title="${name}" aria-label="${name}" style="background:${hex}"></button>`).join('');
  $('cpSwatches').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) { fromHex(b.dataset.hex); render(); }
  });
  $('cpSwatches').addEventListener('dblclick', (e) => { if (e.target.closest('button')) apply(); });
  drag($('cpSV'), (x, y) => { sat = x; val = 1 - y; });
  drag($('cpHue'), (x) => { h = Math.min(359.9, x * 360); });
  $('cpSV').addEventListener('keydown', (e) => {
    const d = e.shiftKey ? 0.1 : 0.02;
    const k = { ArrowLeft: [-d, 0], ArrowRight: [d, 0], ArrowUp: [0, d], ArrowDown: [0, -d] }[e.key];
    if (k) { e.preventDefault(); sat = clamp(sat + k[0], 0, 1); val = clamp(val + k[1], 0, 1); render(); }
    if (e.key === 'Enter') apply();
  });
  $('cpHue').addEventListener('keydown', (e) => {
    const d = (e.shiftKey ? 30 : 5) * ({ ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[e.key] || 0);
    if (d) { e.preventDefault(); h = (h + d + 360) % 360; render(); }
    if (e.key === 'Enter') apply();
  });
  $('cpHex').addEventListener('input', () => {
    const ok = fromHex($('cpHex').value);
    $('cpHex').classList.toggle('bad', !ok && $('cpHex').value.length >= 4);
    if (ok) render(true);
  });
  $('cpHex').addEventListener('keydown', (e) => { if (e.key === 'Enter') apply(); });
  $('cpApply').onclick = apply;
  $('colorBtn').onclick = () => (isOpen() ? close() : open());
  document.addEventListener('pointerdown', (e) => {
    if (isOpen() && !e.target.closest('.colorpick')) close();
  });
  render();
  return { isOpen, close };
})();

$('bannerClose').onclick = () => {
  $('q').value = '';
  $('qNot').value = '';
  setImage(null);
  runView({ mode: 'browse' });
};

let viewSeq = 0;
async function runView(spec, { keepPage = false } = {}) {
  if (!S.state || !S.state.ready) return;
  const seq = ++viewSeq;
  const p = prefs();
  const body = {
    ...spec,
    folders: S.checked.size ? [...S.checked] : null,
    hide_used: $('hideUsed').checked,
    model: $('modelSel').value || null,
    sort: p.sort, desc: p.sort_desc, seed: S.seed,
  };
  $('countInfo').textContent = spec.mode === 'browse' ? 'Loading…' : 'Searching…';
  const slow = setTimeout(() => {
    if (seq === viewSeq && ['text', 'image', 'similar'].includes(spec.mode)) {
      toast('Loading the AI model. The first search after a while takes a few seconds.', '', 6000);
    }
  }, 1500);
  try {
    const v = await api('/api/view', body);
    if (seq !== viewSeq) return;
    S.view = v;
    S.spec = spec;
    const pages = Math.max(1, Math.ceil(v.total / pageSize()));
    S.page = keepPage ? Math.min(S.page, pages - 1) : 0;
    renderBanner();
    await loadPage(keepPage);
  } catch (e) {
    if (seq === viewSeq) {
      fail(e);
      $('countInfo').textContent = '';
    }
  } finally {
    clearTimeout(slow);
  }
}

function rerun(opts = {}) { return runView(S.spec || { mode: 'browse' }, opts); }
function pageSize() { return Number(prefs().page_size) || 500; }

async function loadPage(keepScroll = false) {
  const size = pageSize();
  const offset = S.page * size;
  const scroll = $('viewport').scrollTop;
  try {
    const d = await api(`/api/view/${S.view.view_id}?offset=${offset}&limit=${size}`);
    S.items = d.items;
    S.byId = new Map(d.items.map((it) => [it.id, it]));
    S.sel.clear();
    S.lastIdx = null;
    Grid.setItems(d.items, S.spec.mode !== 'browse' ? offset : null);
    if (keepScroll) $('viewport').scrollTop = scroll;
    updateSelInfo();
    renderPager();
    renderEmpty();
  } catch (e) {
    if (e.status === 410) return rerun();
    fail(e);
  }
}

function renderPager() {
  const total = S.view ? S.view.total : 0;
  const size = pageSize();
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total ? S.page * size + 1 : 0;
  const to = Math.min(total, (S.page + 1) * size);
  const noun = S.spec.mode === 'browse' ? 'clips' : 'results';
  $('countInfo').textContent = total ? `Showing ${fmtNum(from)}–${fmtNum(to)} of ${fmtNum(total)} ${noun}` : '';
  $('pageInfo').textContent = `Page ${S.page + 1} of ${pages}`;
  $('prevPage').disabled = S.page <= 0;
  $('nextPage').disabled = S.page >= pages - 1;
}
$('prevPage').onclick = () => { if (S.page > 0) { S.page--; loadPage(); } };
$('nextPage').onclick = () => { S.page++; loadPage(); };

function renderBanner() {
  const s = S.spec;
  const v = S.view;
  const label = modelLabel(v.model || $('modelSel').value);
  const took = v.took != null ? ` · ${v.took < 1 ? v.took.toFixed(2) : v.took.toFixed(1)} s` : '';
  let text = '';
  if (s.mode === 'text') {
    text = `Results for “${s.query}”${s.exclude && s.exclude.length ? `, not ${s.exclude.join(', ')}` : ''} · ${label}${took}`;
  } else if (s.mode === 'image') {
    text = `Clips that look like ${s.label || 'your image'} · ${label}${took}`;
  } else if (s.mode === 'similar') {
    text = `Similar to ${s.label || 'the chosen clips'} · ${label}${took}`;
  } else if (s.mode === 'audio') {
    text = `Clips where someone says “${s.query}”${took}`;
  } else if (s.mode === 'color') {
    text = `Clips with a lot of ${s.color}${took}`;
  }
  $('bannerText').textContent = text;
  show('banner', s.mode !== 'browse');
  $('note').textContent = v.note || '';
  show('note', !!v.note);
  $('sortSel').disabled = s.mode !== 'browse';
  $('sortDir').disabled = s.mode !== 'browse';
  $('sortSel').title = s.mode !== 'browse' ? 'Results are ordered by best match' : '';
  document.documentElement.style.setProperty('--info-h', s.mode === 'audio' ? '76px' : '58px');
}

function renderEmpty() {
  const e = $('empty');
  let html = '';
  if (!S.items.length) {
    if (!S.folders.length) {
      html = '<div><strong>Add your TikTok folders to get started</strong>Use “+ Add” in the sidebar, or drag folders from Explorer onto the sidebar.</div>';
    } else if (S.spec.mode === 'browse') {
      html = '<div><strong>No clips here</strong>These folders have no videos yet, or “Hide used” is hiding them.</div>';
    } else {
      html = '<div><strong>No matches</strong>If these folders aren\'t indexed yet, press “Index new clips” in the sidebar.</div>';
    }
  }
  e.innerHTML = html;
  show(e, !!html);
}

// ---------------------------------------------------------------------- sort / toolbar
function syncSortControls() {
  const shuffle = prefs().sort === 'shuffle';
  show('reshuffle', shuffle);
  $('sortDir').classList.toggle('hidden', shuffle);
}
$('sortSel').onchange = (e) => {
  savePrefs({ sort: e.target.value });
  syncSortControls();
  if (S.spec.mode === 'browse') rerun();
};
$('sortDir').onclick = () => {
  const desc = !prefs().sort_desc;
  savePrefs({ sort_desc: desc });
  $('sortDir').textContent = desc ? '↓' : '↑';
  if (S.spec.mode === 'browse') rerun();
};
$('reshuffle').onclick = () => { S.seed = Math.floor(Math.random() * 1e9); rerun(); };
$('hideUsed').onchange = (e) => { savePrefs({ hide_used: e.target.checked }); rerun(); };
$('showDetails').onchange = (e) => {
  savePrefs({ show_details: e.target.checked });
  document.body.classList.toggle('no-details', !e.target.checked);
  Grid.relayout();
};
$('tileSize').oninput = (e) => { prefs().tile_width = Number(e.target.value); Grid.relayout(); };
$('tileSize').onchange = (e) => savePrefs({ tile_width: Number(e.target.value) });
$('pageSize').oninput = (e) => { $('pageSizeOut').textContent = e.target.value; };
$('pageSize').onchange = (e) => {
  const first = S.page * pageSize();
  const size = Number(e.target.value);
  savePrefs({ page_size: size });
  const pages = Math.max(1, Math.ceil((S.view ? S.view.total : 0) / size));
  S.page = Math.min(pages - 1, Math.floor(first / size));
  loadPage();
};
$('refreshBtn').onclick = async () => { await loadFolders(); rerun({ keepPage: true }); };

// ---------------------------------------------------------------------- picture search
function setImage(img) {
  S.image = img;
  const box = $('imgDrop');
  box.classList.toggle('has', !!img);
  show('imgThumb', !!img);
  show('imgClear', !!img);
  $('imgLabel').textContent = img ? 'Picture' : 'Search by Image';
  if (img) $('imgThumb').src = img.data || fileUrl(img.path);
  else $('imgThumb').removeAttribute('src');
}
$('imgDrop').onclick = async (e) => {
  if (e.target.id === 'imgClear') return;
  const file = await window.toktidy.pickFile({
    title: 'Search by picture',
    filters: [{ name: 'Images', extensions: ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif'] }],
  });
  if (!file) return;
  setImage({ path: file, name: file.split(/[\\/]/).pop() });
  runSearch();
};
$('imgClear').onclick = (e) => {
  e.stopPropagation();
  setImage(null);
  if (S.spec.mode === 'image') runView({ mode: 'browse' });
};
document.addEventListener('paste', async (e) => {
  if (S.mode !== 'video' || !S.state || !S.state.ready) return;
  const items = [...(e.clipboardData ? e.clipboardData.items : [])];
  if (!items.some((i) => i.type.startsWith('image/'))) return;
  e.preventDefault();
  const data = await window.toktidy.clipboardImage();
  if (!data) return;
  setImage({ data, name: 'the pasted image' });
  runSearch();
});

// ====================================================================== grid
const Grid = {
  items: [],
  nodes: new Map(),
  rankOffset: null,
  cols: 1,
  tileW: 170,
  mediaH: 300,
  rowH: 390,
  raf: null,

  init() {
    const vp = $('viewport');
    vp.addEventListener('scroll', () => this.schedule(), { passive: true });
    new ResizeObserver(() => this.relayout()).observe(vp);
    vp.addEventListener('mousedown', (e) => {
      if (e.target === vp || e.target.id === 'tiles' || e.target.id === 'spacer') { clearSelection(); }
    });
    const layer = $('tiles');
    layer.addEventListener('click', onTileClick);
    layer.addEventListener('dblclick', onTileDblClick);
    layer.addEventListener('contextmenu', onTileMenu);
    layer.addEventListener('dragstart', onTileDragStart);
  },

  schedule() {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => { this.raf = null; this.render(); });
  },

  setItems(items, rankOffset) {
    for (const n of this.nodes.values()) this.dispose(n);
    this.nodes.clear();
    $('tiles').innerHTML = '';
    this.items = items;
    this.rankOffset = rankOffset;
    $('viewport').scrollTop = 0;
    this.layout();
    this.render();
  },

  relayout() {
    this.layout();
    for (const n of this.nodes.values()) n._x = null; // force reposition
    this.render();
  },

  layout() {
    const vp = $('viewport');
    const width = Math.max(100, vp.clientWidth - 28);
    const target = Number(prefs().tile_width) || 170;
    this.cols = Math.max(1, Math.floor((width + GAP) / (target + GAP)));
    this.tileW = (width - GAP * (this.cols - 1)) / this.cols;
    this.mediaH = Math.round((this.tileW * 16) / 9);
    const cs = getComputedStyle(document.documentElement);
    const infoH = document.body.classList.contains('no-details') ? 0 : parseInt(cs.getPropertyValue('--info-h'), 10) || 58;
    const actionsH = parseInt(cs.getPropertyValue('--actions-h'), 10) || 30;
    this.tileH = this.mediaH + infoH + actionsH + 2;
    this.rowH = this.tileH + GAP;
    const rows = Math.ceil(this.items.length / this.cols);
    $('tiles').style.height = `${Math.max(0, rows * this.rowH - GAP)}px`;
  },

  render() {
    const vp = $('viewport');
    const top = Math.max(0, vp.scrollTop - 12);
    const h = vp.clientHeight;
    const rows = Math.ceil(this.items.length / this.cols);
    const first = Math.max(0, Math.floor(top / this.rowH) - 1);
    const last = Math.min(rows - 1, Math.floor((top + h) / this.rowH) + 1);
    const want = new Set();
    for (let r = first; r <= last; r++) {
      for (let c = 0; c < this.cols; c++) {
        const i = r * this.cols + c;
        if (i < this.items.length) want.add(i);
      }
    }
    for (const [i, n] of this.nodes) {
      if (!want.has(i)) { this.dispose(n); this.nodes.delete(i); }
    }
    const layer = $('tiles');
    for (const i of want) {
      let n = this.nodes.get(i);
      if (!n) {
        n = makeTile(this.items[i], i, this.rankOffset);
        layer.appendChild(n);
        this.nodes.set(i, n);
      }
      const x = (i % this.cols) * (this.tileW + GAP);
      const y = Math.floor(i / this.cols) * this.rowH;
      if (n._x !== x || n._y !== y || n._w !== this.tileW) {
        n.style.transform = `translate(${x}px, ${y}px)`;
        n.style.width = `${this.tileW}px`;
        n.style.height = `${this.tileH}px`;
        n.querySelector('.media').style.height = `${this.mediaH}px`;
        n._x = x; n._y = y; n._w = this.tileW;
      }
    }
    this.updatePlayback(top, h);
  },

  updatePlayback(top, h) {
    const max = Number(prefs().max_playing) || 48;
    let playing = 0;
    const sorted = [...this.nodes.entries()].sort((a, b) => a[0] - b[0]);
    for (const [i, n] of sorted) {
      const v = n.querySelector('video');
      if (!v) continue;
      const it = this.items[i];
      const rowTop = Math.floor(i / this.cols) * this.rowH;
      const visible = rowTop < top + h && rowTop + this.tileH > top;
      if (visible && it.preview && playing < max) {
        playing++;
        if (!v.getAttribute('src')) v.src = it.preview;
        if (v.paused) v.play().catch(() => {});
      } else if (!v.paused) {
        v.pause();
      }
    }
  },

  dispose(n) {
    const v = n.querySelector('video');
    if (v) {
      v.pause();
      v.removeAttribute('src');
      v.load();
    }
    n.remove();
  },

  refresh(id) {
    for (const [i, n] of this.nodes) {
      if (this.items[i].id === id) updateTileState(n, this.items[i]);
    }
  },

  refreshAll() {
    for (const [i, n] of this.nodes) updateTileState(n, this.items[i]);
  },

  nodeFor(id) {
    for (const [i, n] of this.nodes) if (this.items[i].id === id) return n;
    return null;
  },
};

function metaLines(it) {
  const a = [];
  if (it.duration != null) a.push(fmtTime(it.duration));
  if (it.width) a.push(`${it.width}×${it.height}`);
  const b = [];
  if (it.fps) b.push(`${Math.round(it.fps * 100) / 100} fps`);
  b.push(fmtSize(it.size));
  return [a.join(' · ') || 'Not indexed yet', b.filter(Boolean).join(' · ')];
}

function makeTile(it, i, rankOffset) {
  const n = document.createElement('div');
  n.className = 'tile';
  n.draggable = true;
  n.dataset.idx = i;
  const ext = (it.ext || '').toLowerCase();
  const badges = [];
  if (PREMIERE_RISKY.has(ext)) badges.push(`<span class="badge warn" title="Premiere may not import this format">${esc(ext.toUpperCase())}</span>`);
  if (rankOffset != null) badges.push(`<span class="badge rank">#${rankOffset + i + 1}</span>`);
  let media;
  if (it.preview) {
    media = `<video muted loop playsinline preload="metadata" disablepictureinpicture ${it.poster ? `poster="${esc(it.poster)}"` : ''}></video>`;
  } else if (it.poster) {
    media = `<img class="poster" src="${esc(it.poster)}" alt=""><div class="ph"></div>`;
  } else if (it.failed) {
    media = `<div class="ph" title="${esc(it.failed)}">Couldn't read this file<br>(see Settings → Library → Failed clips)</div>`;
  } else {
    media = '<div class="ph">Not indexed yet</div>';
  }
  const ml = metaLines(it);
  const match = it.time != null ? `<button class="match" data-act="seek" title="Jump to the matching moment">▶ ${fmtTime(it.time)}</button>` : '';
  n.innerHTML = `
    <div class="media">${media}<div class="badges"><span class="badge used hidden">Used</span>${badges.join('')}</div>${match}</div>
    <div class="info">
      <div class="name" title="${esc(it.path)}">${esc(it.filename)}</div>
      <div class="meta">${esc(ml[0])}</div>
      <div class="meta" title="Folder: ${esc(it.folder)}">${esc(ml[1])} · <span class="folder-name">${esc(it.folder)}</span></div>
      ${it.snippet ? `<div class="snip" title="${esc(it.snippet)}">“${esc(it.snippet)}”</div>` : ''}
    </div>
    <div class="actions">
      <button data-act="open" title="Play the original file">Open</button>
      <button data-act="similar" title="Find clips like this one">Similar</button>
      <button data-act="more" class="more" title="More">⋯</button>
    </div>`;
  updateTileState(n, it);
  return n;
}

function updateTileState(n, it) {
  n.classList.toggle('selected', S.sel.has(it.id));
  n.classList.toggle('used', !!it.used);
  const b = n.querySelector('.badge.used');
  if (b) b.classList.toggle('hidden', !it.used);
}

// ---------------------------------------------------------------------- tile interaction
function tileFromEvent(e) {
  const n = e.target.closest('.tile');
  if (!n) return null;
  const i = Number(n.dataset.idx);
  return { n, i, it: Grid.items[i] };
}

function selectedItems() {
  return Grid.items.filter((it) => S.sel.has(it.id));
}

function updateSelInfo() {
  const n = S.sel.size;
  $('selInfo').textContent = n ? `${n} selected — drag any of them into Premiere` : '';
  show('selClear', n > 0);
}

function clearSelection() {
  if (!S.sel.size) return;
  S.sel.clear();
  Grid.refreshAll();
  updateSelInfo();
}
$('selClear').onclick = clearSelection;

function onTileClick(e) {
  const t = tileFromEvent(e);
  if (!t) return;
  const act = e.target.closest('[data-act]');
  if (act) {
    e.stopPropagation();
    const a = act.dataset.act;
    if (a === 'open') openOriginal(t.it);
    else if (a === 'similar') findSimilar(S.sel.has(t.it.id) && S.sel.size > 1 ? selectedItems() : [t.it]);
    else if (a === 'more') tileMenu(t, e.clientX, e.clientY);
    else if (a === 'seek') seekTile(t.n, t.it);
    return;
  }
  if (e.shiftKey && S.lastIdx != null) {
    const [a, b] = [Math.min(S.lastIdx, t.i), Math.max(S.lastIdx, t.i)];
    if (!e.ctrlKey) S.sel.clear();
    for (let k = a; k <= b; k++) S.sel.add(Grid.items[k].id);
  } else if (e.ctrlKey) {
    if (S.sel.has(t.it.id)) S.sel.delete(t.it.id); else S.sel.add(t.it.id);
    S.lastIdx = t.i;
  } else {
    S.sel.clear();
    S.sel.add(t.it.id);
    S.lastIdx = t.i;
  }
  Grid.refreshAll();
  updateSelInfo();
}

function onTileDblClick(e) {
  const t = tileFromEvent(e);
  if (t && !e.target.closest('[data-act]')) openOriginal(t.it);
}

function onTileMenu(e) {
  const t = tileFromEvent(e);
  if (!t) return;
  e.preventDefault();
  if (!S.sel.has(t.it.id)) {
    S.sel.clear();
    S.sel.add(t.it.id);
    S.lastIdx = t.i;
    Grid.refreshAll();
    updateSelInfo();
  }
  tileMenu(t, e.clientX, e.clientY);
}

function tileMenu(t, x, y) {
  const group = S.sel.has(t.it.id) ? selectedItems() : [t.it];
  const many = group.length > 1;
  const anyUnused = group.some((it) => !it.used);
  openMenu(x, y, [
    { label: 'Open original', action: () => openOriginal(t.it) },
    { label: 'Show in Explorer', action: () => window.toktidy.showInFolder(t.it.path) },
    { label: many ? `Find clips similar to these ${group.length}` : 'Find similar clips', action: () => findSimilar(group) },
    { label: 'Add to Splitscreen View', action: () => group.slice(0, 3).forEach(addToTrio) },
    '-',
    anyUnused
      ? { label: many ? `Mark ${group.length} as used` : 'Mark as used', action: () => markUsed(group.map((i) => i.id), true) }
      : { label: many ? `Mark ${group.length} as not used` : 'Mark as not used', action: () => markUsed(group.map((i) => i.id), false) },
    { label: many ? 'Copy file paths' : 'Copy file path', action: () => { window.toktidy.copyText(group.map((i) => i.path).join('\n')); toast('Copied.', 'ok', 1500); } },
    '-',
    { label: 'Select all on this page', action: () => { Grid.items.forEach((it) => S.sel.add(it.id)); Grid.refreshAll(); updateSelInfo(); } },
  ]);
}

async function openOriginal(it) {
  const err = await window.toktidy.openPath(it.path);
  if (err) toast(`Couldn't open the file: ${err}`, 'error');
}

function seekTile(n, it) {
  const v = n.querySelector('video');
  if (!v || it.time == null) return;
  if (!v.getAttribute('src')) v.src = it.preview;
  const go = () => { v.currentTime = it.time; v.play().catch(() => {}); };
  if (v.readyState >= 1) go(); else v.addEventListener('loadedmetadata', go, { once: true });
}

function findSimilar(items) {
  if (!items.length) return;
  const label = items.length === 1 ? items[0].filename : `${items.length} clips`;
  setImage(null);
  runView({ mode: 'similar', clip_ids: items.map((i) => i.id), label });
}

async function markUsed(ids, used, quiet = false) {
  if (!ids.length) return;
  try {
    await api('/api/clips/used', { ids, used });
    for (const id of ids) {
      const it = S.byId.get(id);
      if (it) it.used = used;
      Grid.refresh(id);
      S.trio.forEach((c) => { if (c && c.id === id) c.used = used; });
    }
    if (!quiet) toast(used ? `Marked ${ids.length} as used.` : `Marked ${ids.length} as not used.`, 'ok', 1800);
  } catch (e) { fail(e); }
}

function onTileDragStart(e) {
  const t = tileFromEvent(e);
  if (!t) return;
  e.preventDefault();
  let group;
  if (S.sel.has(t.it.id)) {
    group = selectedItems();
  } else {
    S.sel.clear();
    S.sel.add(t.it.id);
    S.lastIdx = t.i;
    Grid.refreshAll();
    updateSelInfo();
    group = [t.it];
  }
  nativeDrag(group, t.it.icon);
}

function nativeDrag(group, icon) {
  group = group.filter(Boolean);
  if (!group.length) return;
  window.toktidy.startDrag(group.map((i) => i.path), icon || (group[0] && group[0].icon));
  const autoMark = !!prefs().auto_mark_used;
  const toMark = group.filter((i) => !i.used).map((i) => i.id);
  S.lastDrag = { at: Date.now(), paths: new Set(group.map((i) => i.path)), marked: autoMark ? toMark : [] };
  if (autoMark && toMark.length) markUsed(toMark, true, true);
}

// ====================================================================== drops (trio, picture search, folders)
function dropTargetKind(el) {
  if (!el) return null;
  if (el.closest('.slot')) return 'slot';
  if (el.closest('#imgDrop')) return 'image';
  if (el.closest('.sidebar')) return 'folders';
  if (el.closest('#welcome')) return 'folders';
  return 'other';
}

window.addEventListener('dragover', (e) => {
  e.preventDefault();
  const kind = dropTargetKind(e.target);
  document.querySelectorAll('.over').forEach((x) => x.classList.remove('over'));
  if (kind === 'slot') e.target.closest('.slot').classList.add('over');
  if (kind === 'image') $('imgDrop').classList.add('over');
  const internal = S.lastDrag && Date.now() - S.lastDrag.at < 60000;
  show('dropHint', kind === 'folders' && !internal);
  e.dataTransfer.dropEffect = 'copy';
});
window.addEventListener('dragleave', (e) => {
  if (!e.relatedTarget) {
    show('dropHint', false);
    document.querySelectorAll('.over').forEach((x) => x.classList.remove('over'));
  }
});
window.addEventListener('drop', async (e) => {
  e.preventDefault();
  show('dropHint', false);
  document.querySelectorAll('.over').forEach((x) => x.classList.remove('over'));
  const files = [...e.dataTransfer.files].map((f) => ({ path: window.toktidy.pathForFile(f), type: f.type, name: f.name }))
    .filter((f) => f.path);
  const kind = dropTargetKind(e.target);

  // A clip dragged out of this app landed back inside it: it didn't go to Premiere
  const last = S.lastDrag;
  const internal = last && Date.now() - last.at < 60000 && files.length && files.every((f) => last.paths.has(f.path));
  if (internal) {
    if (last.marked.length) markUsed(last.marked, false, true);
    S.lastDrag = null;
  }

  if (kind === 'slot') {
    const slot = Number(e.target.closest('.slot').dataset.slot);
    if (!files.length) return;
    try {
      const { ids } = await api('/api/clips/by-paths', { paths: files.slice(0, 3).map((f) => f.path) });
      if (!ids.length) return toast('That file isn\'t in your library yet.');
      const { items } = await api('/api/clips/get', { ids });
      items.forEach((it, k) => setTrioSlot(Math.min(2, slot + k), it));
    } catch (err) { fail(err); }
    return;
  }
  if (kind === 'image' || (!internal && files.length === 1 && /^image\//.test(files[0].type) && kind === 'other')) {
    const img = files.find((f) => /^image\//.test(f.type) || /\.(jpe?g|png|webp|bmp|gif)$/i.test(f.path));
    if (!img) return toast('Drop a picture (jpg, png, webp) to search by image.');
    setMode('video');
    setImage({ path: img.path, name: img.name });
    runSearch();
    return;
  }
  if (kind === 'folders' && !internal && files.length) {
    try {
      const r = await api('/api/folders/add', { paths: files.map((f) => f.path) });
      if (!r.added.length) return toast('Nothing new to add. Drop folders, not files.');
      toast(`Added ${r.added.length} folder(s).`, 'ok');
      if (!$('welcome').classList.contains('hidden')) {
        const folders = await api('/api/folders');
        $('wFolderList').innerHTML = folders.map((f) => `<li>${esc(f.name)} <span class="muted">— ${fmtNum(f.clips)} clips</span></li>`).join('');
        $('wToStep3').disabled = folders.length === 0;
      } else {
        await loadFolders();
        rerun();
      }
    } catch (err) { fail(err); }
  }
});

// ====================================================================== trio
$('trioBtn').onclick = () => toggleTrio();
$('trioClose').onclick = () => toggleTrio(false);
function toggleTrio(on) {
  const el = $('trio');
  const next = on === undefined ? el.classList.contains('hidden') : on;
  show(el, next);
  $('trioBtn').classList.toggle('primary', next);
  if (next) renderTrio();
  else el.querySelectorAll('video').forEach((v) => v.pause());
}

function addToTrio(it) {
  const k = S.trio.findIndex((c) => !c);
  if (k === -1) {
    toast('Splitscreen View is full. Remove a clip first.');
    return;
  }
  setTrioSlot(k, it);
}

function setTrioSlot(k, it) {
  S.trio[k] = it;
  toggleTrio(true);
}

function renderTrio() {
  document.querySelectorAll('.slot').forEach((slot) => {
    const k = Number(slot.dataset.slot);
    const it = S.trio[k];
    const v = slot.querySelector('video');
    if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
    if (!it) {
      slot.innerHTML = `<span class="small">Clip ${k + 1}</span>`;
      slot.draggable = false;
      return;
    }
    slot.draggable = true;
    slot.innerHTML = `
      ${it.preview ? `<video muted loop playsinline autoplay ${it.poster ? `poster="${esc(it.poster)}"` : ''} src="${esc(it.preview)}"></video>` : '<span class="small">No preview yet</span>'}
      <div class="slot-bar">
        <span title="${esc(it.path)}">${esc(it.filename)}</span>
        <button data-slot-act="open" title="Open original">Open</button>
        <button data-slot-act="remove" class="x" title="Remove">×</button>
      </div>`;
  });
  const n = S.trio.filter(Boolean).length;
  $('trioSimilar').disabled = n === 0;
  $('trioRestart').disabled = n === 0;
  $('trioDragAll').style.opacity = n ? 1 : 0.4;
}

document.querySelector('.trio-slots').addEventListener('click', (e) => {
  const b = e.target.closest('[data-slot-act]');
  if (!b) return;
  const k = Number(e.target.closest('.slot').dataset.slot);
  if (b.dataset.slotAct === 'remove') { S.trio[k] = null; renderTrio(); }
  if (b.dataset.slotAct === 'open') openOriginal(S.trio[k]);
});
document.querySelector('.trio-slots').addEventListener('dragstart', (e) => {
  const slot = e.target.closest('.slot');
  if (!slot) return;
  const it = S.trio[Number(slot.dataset.slot)];
  e.preventDefault();
  if (it) nativeDrag([it], it.icon);
});
$('trioDragAll').addEventListener('dragstart', (e) => {
  e.preventDefault();
  const clips = S.trio.filter(Boolean);
  if (clips.length) nativeDrag(clips, clips[0].icon);
});
$('trioRestart').onclick = () => {
  const vids = [...document.querySelectorAll('.slot video')];
  vids.forEach((v) => { v.pause(); v.currentTime = 0; });
  vids.forEach((v) => v.play().catch(() => {}));
};
$('trioSimilar').onclick = () => findSimilar(S.trio.filter(Boolean));
$('trioClear').onclick = () => { S.trio = [null, null, null]; renderTrio(); };

// ====================================================================== settings
document.querySelectorAll('.tabs button').forEach((b) => { b.onclick = () => selectTab(b.dataset.tab); });
function selectTab(name) {
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach((t) => show(t, t.dataset.tab === name));
  if (name === 'library') { renderCacheParts(); loadFailed(); }
}
$('settingsBtn').onclick = () => openSettings('general');
$('settingsClose').onclick = () => closeSettings();
$('settings').addEventListener('mousedown', (e) => { if (e.target.id === 'settings') closeSettings(); });

function getPath(obj, path) { return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj); }

async function openSettings(tab) {
  try { S.settings = await api('/api/settings'); } catch (e) { return fail(e); }
  // model list
  const models = S.settings.models;
  $('setDefaultModel').innerHTML = Object.entries(models)
    .map(([k, m]) => `<option value="${esc(k)}">${esc(m.label || k)}</option>`).join('')
    + `<option value="${BOTH}">Both models (merged results)</option>`;
  $('modelToggles').innerHTML = Object.entries(models).map(([k, m]) => `
    <div class="row">
      <label class="switch" style="flex:1"><input type="checkbox" data-setting="models.${esc(k)}.enabled"> Index and search with ${esc(m.label || k)}</label>
      <button data-reset-model="${esc(k)}" class="link">Clear its search data…</button>
    </div>`).join('');
  document.querySelectorAll('[data-reset-model]').forEach((b) => {
    b.onclick = async () => {
      const k = b.dataset.resetModel;
      const ok = await confirmBox('Clear search data?',
        `All ${models[k].label || k} search data will be deleted and rebuilt from the saved frames on the next index run. Your previews and frames are kept.`, 'Clear', true);
      if (!ok) return;
      try { const r = await api('/api/models/reset', { model: k }); toast(`Cleared ${fmtNum(r.cleared)} clips.`, 'ok'); } catch (e) { fail(e); }
    };
  });
  document.querySelectorAll('#settings [data-setting]').forEach((input) => {
    const v = getPath(S.settings, input.dataset.setting);
    if (input.type === 'checkbox') input.checked = !!v;
    else input.value = v ?? '';
  });
  show('settings');
  selectTab(tab || 'general');
}

function closeSettings() {
  show('settings', false);
}

document.getElementById('settings').addEventListener('change', async (e) => {
  const input = e.target.closest('[data-setting]');
  if (!input) return;
  const key = input.dataset.setting;
  let value;
  if (input.type === 'checkbox') value = input.checked;
  else if (input.dataset.type === 'int') value = parseInt(input.value, 10);
  else if (input.dataset.type === 'float') value = parseFloat(input.value);
  else value = input.value;
  if (typeof value === 'number' && Number.isNaN(value)) return;
  const patch = {};
  const parts = key.split('.');
  let o = patch;
  parts.slice(0, -1).forEach((p) => { o[p] = {}; o = o[p]; });
  o[parts[parts.length - 1]] = value;
  try {
    S.settings = await api('/api/settings', patch);
    S.state = await api('/api/state');
    if (key.startsWith('models.') || key === 'default_model') fillModelSelect();
    if (key === 'app.show_details') { applyPrefs(); Grid.relayout(); }
    if (key === 'app.max_playing') Grid.render();
    toast('Saved.', 'ok', 1200);
  } catch (err) { fail(err); }
});

$('resetUsedSel').onclick = () => {
  if (!S.checked.size) return toast('Tick one or more folders in the sidebar first.');
  resetUsed([...S.checked]);
};
$('resetUsedAll').onclick = () => resetUsed(null);

// ---------------------------------------------------------------------- library & cache tab
const PART_LABELS = { database: 'Library file', frames: 'Frames', previews: 'Previews', embeddings: 'Search data' };
function renderCacheParts(sizes) {
  const paths = S.state.paths || {};
  $('cacheParts').innerHTML = Object.keys(PART_LABELS).map((p) => {
    const sz = sizes && sizes[p] ? ` <span class="muted">(${fmtSize(sizes[p].bytes) || '0 KB'}, ${fmtNum(sizes[p].files)} files)</span>` : '';
    return `<div class="cachepart"><strong>${PART_LABELS[p]}</strong>
      <code title="${esc(paths[p])}">${esc(paths[p] || '')}</code>${sz ? `<span>${sz}</span>` : '<span></span>'}
      <span><button data-move="${p}">Move…</button> <button data-relink2="${p}" class="link">Moved it myself…</button></span></div>`;
  }).join('');
  document.querySelectorAll('[data-move]').forEach((b) => {
    b.onclick = async () => {
      const part = b.dataset.move;
      const [dir] = await window.toktidy.pickFolder({ title: `Move ${PART_LABELS[part].toLowerCase()} to…` });
      if (!dir) return;
      const ok = await confirmBox(`Move ${PART_LABELS[part].toLowerCase()}?`,
        `Everything will be copied to ${dir}, checked, and only then removed from the old place. This can take a long time for previews and frames.`, 'Move');
      if (!ok) return;
      try { await api('/api/cache/move', { part, dest: dir }); watchTask(); } catch (e) { fail(e); }
    };
  });
  document.querySelectorAll('[data-relink2]').forEach((b) => {
    b.onclick = async () => {
      const part = b.dataset.relink2;
      const p = part === 'database'
        ? await window.toktidy.pickFile({ title: 'Find library.db', filters: [{ name: 'TokTidy library', extensions: ['db'] }] })
        : (await window.toktidy.pickFolder({ title: `Find the ${part} folder` }))[0];
      if (!p) return;
      try {
        S.state = await api('/api/cache/relink', { part, path: p });
        renderCacheParts();
        toast('Relinked.', 'ok');
      } catch (e) { fail(e); }
    };
  });
}

$('measureBtn').onclick = async () => {
  try { await api('/api/cache/measure', {}); watchTask((res) => renderCacheParts(res)); } catch (e) { fail(e); }
};
$('cleanupBtn').onclick = async () => {
  const ok = await confirmBox('Forget deleted clips?',
    'Clips that no longer exist on disk are removed from the library, along with their previews and search data. Tip: press “Index new clips” first so moved clips are recognised.', 'Forget them');
  if (!ok) return;
  try { await api('/api/cleanup', {}); watchTask(() => { loadFolders(); rerun({ keepPage: true }); }); } catch (e) { fail(e); }
};

async function watchTask(onDone) {
  show('taskOut');
  for (;;) {
    let t;
    try { t = await api('/api/task'); } catch (e) { fail(e); return; }
    if (!t) return;
    $('taskOut').textContent = t.messages.join('\n') + (t.error ? `\nError: ${t.error}` : '');
    if (!t.running) {
      if (t.error) toast(t.error, 'error');
      else {
        S.state = await api('/api/state');
        if (onDone) onDone(t.result);
      }
      if (S.state.ready) renderCacheParts(t.name === 'Measure cache' ? t.result : undefined);
      return;
    }
    await sleep(800);
  }
}

async function loadFailed() {
  try {
    const rows = await api('/api/failed');
    $('failedList').innerHTML = rows.length
      ? rows.map((r) => `<div><strong>${esc(r.filename)}</strong> <span class="muted">in ${esc(r.folder)}</span><small>${esc(r.stages)}: ${esc(r.error)}</small></div>`).join('')
      : '<div class="muted">No failed clips.</div>';
  } catch (e) { fail(e); }
}
$('failedRefresh').onclick = loadFailed;
$('failedRetry').onclick = async () => {
  try {
    const r = await api('/api/failed/retry', {});
    toast(`${fmtNum(r.reset)} steps will be retried on the next index run.`, 'ok');
    loadFailed();
    loadFolders();
  } catch (e) { fail(e); }
};

// ---------------------------------------------------------------------- diagnostics tab
let lastDiag = null;
$('diagRun').onclick = async () => {
  $('diagRun').disabled = true;
  $('diagList').innerHTML = '<li class="muted">Running checks…</li>';
  try {
    lastDiag = await api(`/api/diagnostics?whisper=${$('diagWhisper').checked}`);
    $('diagList').innerHTML = lastDiag.checks.map(([st, msg]) => `<li class="${st}"><b>${st}</b> ${esc(msg)}</li>`).join('');
    $('diagLog').textContent = lastDiag.log.join('\n');
  } catch (e) { fail(e); } finally { $('diagRun').disabled = false; }
};
$('diagCopy').onclick = async () => {
  const appInfo = await window.toktidy.appInfo();
  let d = lastDiag;
  if (!d) { try { d = await api('/api/diagnostics'); } catch { d = null; } }
  const engineLog = await window.toktidy.backendLog();
  const lines = [
    '=== TokTidy diagnostics ===',
    `App: ${JSON.stringify(appInfo)}`,
    '',
    '--- Checks ---',
    ...(d ? d.checks.map(([s, m]) => `${s.padEnd(5)} ${m}`) : ['(engine not reachable)']),
    '',
    d ? `Clips: ${d.clips}  Folders: ${d.folders}  Models in GPU: ${(d.models_loaded || []).join(', ') || 'none'}  VRAM used: ${d.vram_used_gb ?? '?'} GB` : '',
    d ? `Stages: ${JSON.stringify(d.stages)}` : '',
    '',
    '--- Index job ---',
    JSON.stringify(S.job),
    '',
    '--- Failed samples ---',
    ...(d && d.failed_samples ? d.failed_samples.map((f) => `${f.filename} [${f.vcodec}] ${f.stage}: ${f.error}`) : []),
    '',
    '--- Settings ---',
    d ? JSON.stringify(d.settings, null, 1) : '',
    '',
    '--- Engine log ---',
    ...(d ? d.log : []),
    '',
    '--- Engine console ---',
    ...engineLog.slice(-80),
  ];
  await window.toktidy.copyText(lines.join('\n'));
  toast('Diagnostics copied. Paste them into the chat.', 'ok');
};
$('diagLogs').onclick = () => window.toktidy.openLogs();

// ====================================================================== keyboard
document.addEventListener('keydown', (e) => {
  const typing = e.target.matches('input, select, textarea');
  if (e.key === 'Escape') {
    if (!$('menu').classList.contains('hidden')) return closeMenu();
    if (ColorPicker.isOpen()) return ColorPicker.close(true);
    if (!$('dialog').classList.contains('hidden')) return $('dlgCancel').click();
    if (!$('settings').classList.contains('hidden')) return closeSettings();
    if (!typing) clearSelection();
    return;
  }
  if (e.ctrlKey && e.key.toLowerCase() === 'f') {
    e.preventDefault();
    $('q').focus();
    $('q').select();
    return;
  }
  if (typing || $('main').classList.contains('hidden')) return;
  if (e.ctrlKey && e.key.toLowerCase() === 'a') {
    e.preventDefault();
    Grid.items.forEach((it) => S.sel.add(it.id));
    Grid.refreshAll();
    updateSelInfo();
  } else if (e.key === 'Enter' && S.sel.size === 1) {
    openOriginal(selectedItems()[0]);
  } else if (e.altKey && e.key === 'ArrowRight' && !$('nextPage').disabled) {
    $('nextPage').click();
  } else if (e.altKey && e.key === 'ArrowLeft' && !$('prevPage').disabled) {
    $('prevPage').click();
  }
});
document.addEventListener('mousedown', (e) => {
  if (!e.target.closest('#menu')) closeMenu();
});
window.addEventListener('blur', closeMenu);

// ====================================================================== go
boot().catch((e) => showFatal(e.message, []));
