// TokTidy desktop app - main process.
// Starts the Python engine, opens the window, and handles things only the
// operating system can do (native drag to Premiere, file dialogs, Explorer).
const { app, BrowserWindow, ipcMain, dialog, shell, nativeImage, clipboard, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { spawn } = require('child_process');

// Two layouts are supported:
//  - installed (by the TokTidy installer):  <install>\TokTidy.exe, <install>\resources\app\ (this file),
//    <install>\engine\{backend,env,ffmpeg}, <install>\models
//  - from source (setup.bat):                <repo>\app\ (this file), <repo>\backend, <repo>\.venv
const INSTALL_ROOT = path.resolve(__dirname, '..', '..');
const INSTALLED = fs.existsSync(path.join(INSTALL_ROOT, 'engine', 'backend', 'toktidy'));
const ROOT = INSTALLED ? INSTALL_ROOT : path.resolve(__dirname, '..');
const BACKEND_DIR = INSTALLED ? path.join(ROOT, 'engine', 'backend') : path.join(ROOT, 'backend');
const FFMPEG_DIR = INSTALLED ? path.join(ROOT, 'engine', 'ffmpeg', 'bin') : null;
const MODELS_DIR = INSTALLED ? path.join(ROOT, 'models') : null;
const PYTHON = process.env.TOKTIDY_PYTHON || (INSTALLED
  ? path.join(ROOT, 'engine', 'env', 'Scripts', 'python.exe')
  : (process.platform === 'win32'
    ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT, '.venv', 'bin', 'python')));

// In the installed layout, run the real Python (it can be moved with the folder)
// rather than the venv's python.exe, which remembers the old location.
function findBasePython() {
  if (!INSTALLED) return null;
  const dir = path.join(ROOT, 'engine', 'python');
  let names = [];
  try { names = fs.readdirSync(dir); } catch { return null; }
  const found = names
    .filter((n) => /^cpython-3\.\d+\.\d+/i.test(n))
    .filter((n) => {
      try { return !fs.lstatSync(path.join(dir, n)).isSymbolicLink(); } catch { return false; }   // skip junctions
    })
    .filter((n) => fs.existsSync(path.join(dir, n, 'python.exe')))
    .sort((a, b) => {
      const va = a.match(/(\d+)\.(\d+)\.(\d+)/).slice(1).map(Number);
      const vb = b.match(/(\d+)\.(\d+)\.(\d+)/).slice(1).map(Number);
      return va[0] - vb[0] || va[1] - vb[1] || va[2] - vb[2];
    });
  return found.length ? path.join(dir, found[found.length - 1], 'python.exe') : null;
}
const SITE_PACKAGES = INSTALLED ? path.join(ROOT, 'engine', 'env', 'Lib', 'site-packages') : null;
const BASE_PYTHON = process.env.TOKTIDY_PYTHON ? null
  : (fs.existsSync(SITE_PACKAGES || '') ? findBasePython() : null);
const ENGINE_PYTHON = BASE_PYTHON || PYTHON;

if (process.platform === 'win32') app.setAppUserModelId('com.toktidy.app');

let mainWindow = null;
let backend = null;
let backendPromise = null;
let backendLog = [];
let quitting = false;

// Small fallback drag icon (used when a clip has no frame yet)
const FALLBACK_ICON = nativeImage.createFromPath(path.join(__dirname, 'assets', 'drag.png'));

function logLine(line) {
  backendLog.push(line);
  if (backendLog.length > 600) backendLog.shift();
}

function startBackend() {
  backendLog = [];
  return new Promise((resolve, reject) => {
    if (!fs.existsSync(ENGINE_PYTHON)) {
      reject(new Error(INSTALLED
        ? `TokTidy's AI engine is missing:\n${ENGINE_PYTHON}\n\nRun the TokTidy installer again to repair it.`
        : `TokTidy's Python environment was not found:\n${PYTHON}\n\nRun setup.bat first.`));
      return;
    }
    const token = crypto.randomBytes(16).toString('hex');
    const env = {
      ...process.env,
      PYTHONPATH: BACKEND_DIR,
      PYTHONUTF8: '1',
      PYTHONUNBUFFERED: '1',
      TOKTIDY_TOKEN: token,
      TOKTIDY_PARENT_PID: String(process.pid),
      HF_HUB_DISABLE_SYMLINKS_WARNING: '1',
      TOKENIZERS_PARALLELISM: 'false',
    };
    if (FFMPEG_DIR && fs.existsSync(FFMPEG_DIR)) {
      const key = Object.keys(env).find((k) => k.toLowerCase() === 'path') || 'PATH';
      env[key] = `${FFMPEG_DIR}${path.delimiter}${env[key] || ''}`;
    }
    if (MODELS_DIR) {
      env.HF_HOME = MODELS_DIR;
      env.TORCH_HOME = path.join(MODELS_DIR, 'torch');
    }
    if (BASE_PYTHON) env.TOKTIDY_SITE_PACKAGES = SITE_PACKAGES;
    const args = BASE_PYTHON ? ['-m', 'toktidy._boot', 'toktidy.server'] : ['-m', 'toktidy.server'];
    const proc = spawn(ENGINE_PYTHON, args, { cwd: ROOT, env, windowsHide: true });
    backend = proc;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error('The TokTidy engine did not start within 2 minutes.\n\n' + backendLog.slice(-40).join('\n')));
    }, 120000);

    let partial = '';
    const onData = (buf) => {
      const text = partial + buf.toString('utf8');
      const lines = text.split(/\r?\n/);
      partial = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        logLine(line);
        const m = line.match(/^TOKTIDY_READY (\d+)/);
        if (m && !settled) {
          settled = true;
          clearTimeout(timer);
          resolve({ url: `http://127.0.0.1:${m[1]}`, token });
        }
      }
    };
    proc.stdout.on('data', onData);
    proc.stderr.on('data', onData);
    proc.on('error', (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`Could not start Python: ${err.message}`));
    });
    proc.on('exit', (code) => {
      if (backend !== proc) return; // an older engine we already replaced
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`The TokTidy engine stopped while starting (exit code ${code}).\n\n` +
          backendLog.slice(-40).join('\n')));
        return;
      }
      backendPromise = null; // the next request starts a fresh engine
      backend = null;
      if (!quitting && mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-exit', { code, log: backendLog.slice(-60) });
      }
    });
  });
}

function stopBackend() {
  if (backend && backend.exitCode === null) {
    try { backend.kill(); } catch (e) { /* already gone */ }
  }
  backend = null;
}

// ------------------------------------------------------------------ shortcut repair
// If the TokTidy folder was moved, the Desktop shortcut still points at the
// old place. Each start, point it at this copy (only if the shortcut exists).
// Installed copy moved to another folder: point its shortcuts and its
// "Installed apps" entry at the new place.
function repairInstalledLocation() {
  if (process.platform !== 'win32' || !INSTALLED) return;
  const exe = process.execPath;
  const same = (a, b) => !!a && !!b && path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
  const links = [
    path.join(app.getPath('desktop'), 'TokTidy.lnk'),
    path.join(app.getPath('appData'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'TokTidy.lnk'),
  ];
  for (const lnk of links) {
    try {
      if (!fs.existsSync(lnk)) continue;
      let cur = {};
      try { cur = shell.readShortcutLink(lnk); } catch { /* rewrite */ }
      if (same(cur.target, exe)) continue;
      shell.writeShortcutLink(lnk, 'replace', { target: exe, args: '', cwd: ROOT, icon: exe, iconIndex: 0, description: 'TokTidy' });
      logLine(`Shortcut updated: ${lnk}`);
    } catch (e) { logLine(`Shortcut check skipped: ${e.message}`); }
  }
  const { execFile } = require('child_process');
  const reg = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'reg.exe');
  const key = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\TokTidy';
  execFile(reg, ['query', key, '/v', 'InstallLocation'], { windowsHide: true }, (err, out) => {
    if (err) return;   // not installed by the installer
    const m = /InstallLocation\s+REG_\w+\s+(.+)/i.exec(out || '');
    if (m && same(m[1].trim(), ROOT)) return;
    const uninst = path.join(ROOT, 'Uninstall TokTidy.exe');
    const values = [
      ['InstallLocation', ROOT],
      ['DisplayIcon', `${exe},0`],
      ['UninstallString', `"${uninst}"`],
      ['QuietUninstallString', `"${uninst}" /S`],
    ];
    for (const [name, data] of values) {
      execFile(reg, ['add', key, '/v', name, '/t', 'REG_SZ', '/d', data, '/f'], { windowsHide: true }, () => {});
    }
    execFile(reg, ['add', 'HKCU\\Software\\TokTidy', '/v', 'InstallDir', '/t', 'REG_SZ', '/d', ROOT, '/f'], { windowsHide: true }, () => {});
    logLine(`Installed location updated to ${ROOT}`);
  });
}

function repairDesktopShortcut() {
  if (process.platform !== 'win32') return;
  if (INSTALLED) { repairInstalledLocation(); return; }
  try {
    const lnk = path.join(app.getPath('desktop'), 'TokTidy.lnk');
    if (!fs.existsSync(lnk)) return;
    const launcher = path.join(ROOT, 'TokTidy.exe');
    const want = fs.existsSync(launcher)
      ? { target: launcher, args: '', icon: launcher }
      : { target: process.execPath, args: `"${path.join(ROOT, 'app')}"`, icon: path.join(__dirname, 'assets', 'icon.ico') };
    let cur = {};
    try { cur = shell.readShortcutLink(lnk); } catch { /* unreadable: rewrite it */ }
    const same = (a, b) => !!a && !!b && path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
    if (same(cur.target, want.target) && (cur.args || '') === want.args && same(cur.cwd, ROOT)) return;
    const ok = shell.writeShortcutLink(lnk, 'replace', {
      target: want.target, args: want.args, cwd: ROOT,
      icon: want.icon, iconIndex: 0, description: 'TokTidy',
    });
    logLine(ok ? `Desktop shortcut updated to ${want.target}` : 'Could not update the Desktop shortcut');
  } catch (e) {
    logLine(`Shortcut check skipped: ${e.message}`);
  }
}

// ------------------------------------------------------------------ window

const stateFile = () => path.join(app.getPath('userData'), 'window.json');

function loadWindowState() {
  try { return JSON.parse(fs.readFileSync(stateFile(), 'utf8')); } catch { return { width: 1500, height: 920 }; }
}

function saveWindowState() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const b = mainWindow.getNormalBounds();
  try {
    fs.writeFileSync(stateFile(), JSON.stringify({ ...b, maximized: mainWindow.isMaximized() }));
  } catch { /* ignore */ }
}

function createWindow() {
  const st = loadWindowState();
  mainWindow = new BrowserWindow({
    x: st.x, y: st.y, width: st.width || 1500, height: st.height || 920,
    minWidth: 900, minHeight: 600,
    backgroundColor: '#1a1c21',
    title: 'TokTidy',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  });
  if (st.maximized) mainWindow.maximize();
  Menu.setApplicationMenu(null);
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // Never let a dropped file replace the app page
  mainWindow.webContents.on('will-navigate', (e) => e.preventDefault());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.type === 'keyDown' && (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i'))) {
      mainWindow.webContents.toggleDevTools();
    }
    if (input.type === 'keyDown' && input.key === 'F5' && input.control) {
      mainWindow.webContents.reload();
    }
  });
  mainWindow.on('close', saveWindowState);
  mainWindow.on('closed', () => { mainWindow = null; });
}

// ------------------------------------------------------------------ IPC

ipcMain.handle('backend-info', async () => {
  if (!backendPromise) backendPromise = startBackend();
  try {
    return { ok: true, ...(await backendPromise) };
  } catch (err) {
    backendPromise = null;
    return { ok: false, error: err.message, log: backendLog.slice(-80) };
  }
});

ipcMain.handle('restart-backend', async () => {
  stopBackend();
  backendPromise = startBackend();
  try {
    return { ok: true, ...(await backendPromise) };
  } catch (err) {
    backendPromise = null;
    return { ok: false, error: err.message, log: backendLog.slice(-80) };
  }
});

ipcMain.handle('backend-log', () => backendLog.slice(-300));

ipcMain.handle('pick-folder', async (_e, opts = {}) => {
  const res = await dialog.showOpenDialog(mainWindow, {
    title: opts.title || 'Choose a folder',
    properties: ['openDirectory', ...(opts.multi ? ['multiSelections'] : []), 'createDirectory'],
  });
  return res.canceled ? [] : res.filePaths;
});

ipcMain.handle('pick-file', async (_e, opts = {}) => {
  const res = await dialog.showOpenDialog(mainWindow, {
    title: opts.title || 'Choose a file',
    properties: ['openFile'],
    filters: opts.filters || [],
  });
  return res.canceled ? null : res.filePaths[0];
});

ipcMain.on('start-drag', (event, files, iconPath) => {
  const list = (files || []).filter((f) => f && fs.existsSync(f));
  if (!list.length) return;
  let icon = iconPath && fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : FALLBACK_ICON;
  if (icon.isEmpty()) icon = FALLBACK_ICON;
  const size = icon.getSize();
  if (size.width > 96) icon = icon.resize({ width: 96 });
  try {
    event.sender.startDrag({ file: list[0], files: list, icon });
  } catch (err) {
    console.error('startDrag failed', err);
  }
});

ipcMain.handle('open-path', async (_e, p) => {
  const err = await shell.openPath(p);
  return err || null;
});
ipcMain.handle('show-in-folder', (_e, p) => { shell.showItemInFolder(p); return true; });
ipcMain.handle('copy-text', (_e, t) => { clipboard.writeText(String(t)); return true; });
ipcMain.handle('clipboard-image', () => {
  const img = clipboard.readImage();
  if (img.isEmpty()) return null;
  const s = img.getSize();
  const scaled = s.width > 1024 ? img.resize({ width: 1024 }) : img;
  return scaled.toDataURL();
});
ipcMain.handle('open-logs', () => {
  const dir = path.join(process.env.APPDATA || app.getPath('appData'), 'TokTidy', 'logs');
  fs.mkdirSync(dir, { recursive: true });
  shell.openPath(dir);
  return dir;
});
ipcMain.handle('app-info', () => ({
  version: app.getVersion(),
  electron: process.versions.electron,
  chrome: process.versions.chrome,
  platform: `${process.platform} ${process.getSystemVersion ? process.getSystemVersion() : ''}`,
  root: ROOT,
  python: ENGINE_PYTHON,
}));

// ------------------------------------------------------------------ lifecycle

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(() => {
    backendPromise = startBackend();
    backendPromise.catch(() => {}); // reported to the window through backend-info
    createWindow();
    setTimeout(repairDesktopShortcut, 3000);
  });
  app.on('before-quit', () => { quitting = true; stopBackend(); });
  app.on('window-all-closed', () => { quitting = true; stopBackend(); app.quit(); });
}
