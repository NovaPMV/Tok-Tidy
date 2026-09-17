// Safe bridge between the app page and the main process.
const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('toktidy', {
  backend: () => ipcRenderer.invoke('backend-info'),
  restartBackend: () => ipcRenderer.invoke('restart-backend'),
  backendLog: () => ipcRenderer.invoke('backend-log'),
  onBackendExit: (cb) => ipcRenderer.on('backend-exit', (_e, d) => cb(d)),
  pickFolder: (opts) => ipcRenderer.invoke('pick-folder', opts),
  pickFile: (opts) => ipcRenderer.invoke('pick-file', opts),
  startDrag: (files, icon) => ipcRenderer.send('start-drag', files, icon),
  openPath: (p) => ipcRenderer.invoke('open-path', p),
  showInFolder: (p) => ipcRenderer.invoke('show-in-folder', p),
  copyText: (t) => ipcRenderer.invoke('copy-text', t),
  clipboardImage: () => ipcRenderer.invoke('clipboard-image'),
  openLogs: () => ipcRenderer.invoke('open-logs'),
  appInfo: () => ipcRenderer.invoke('app-info'),
  pathForFile: (file) => {
    try { return webUtils.getPathForFile(file) || null; } catch { return null; }
  },
});
