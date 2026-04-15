const { contextBridge, ipcRenderer } = require('electron');

// Expose a minimal API to the renderer so it can detect Electron
// without needing nodeIntegration.
contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform: process.platform,
  /** Opens a native folder-picker dialog. Returns the selected path or null. */
  selectFolder: () => ipcRenderer.invoke('select-folder'),
  localSync: {
    start: (payload) => ipcRenderer.invoke('local-sync/start', payload),
    stop: (connectorId) => ipcRenderer.invoke('local-sync/stop', { connectorId }),
    status: (connectorId) => ipcRenderer.invoke('local-sync/status', { connectorId }),
    replay: (connectorId) => ipcRenderer.invoke('local-sync/replay', { connectorId }),
    rescan: (connectorId) => ipcRenderer.invoke('local-sync/rescan', { connectorId }),
    fullResync: (connectorId) => ipcRenderer.invoke('local-sync/full-resync', { connectorId }),
    setSchedule: (payload) => ipcRenderer.invoke('local-sync/set-schedule', payload),
    onStatus: (callback) => {
      const listener = (_event, payload) => callback(payload);
      ipcRenderer.on('local-sync-status', listener);
      return () => ipcRenderer.removeListener('local-sync-status', listener);
    },
  },
});
