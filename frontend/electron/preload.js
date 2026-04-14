const { contextBridge, ipcRenderer } = require('electron');

// Expose a minimal API to the renderer so it can detect Electron
// without needing nodeIntegration.
contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform: process.platform,
  /** Opens a native folder-picker dialog. Returns the selected path or null. */
  selectFolder: () => ipcRenderer.invoke('select-folder'),
});
