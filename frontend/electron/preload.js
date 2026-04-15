const { contextBridge, ipcRenderer } = require('electron');

// Expose a minimal API to the renderer so it can detect Electron
// without needing nodeIntegration.
let nextStreamId = 1;

// Stable for the lifetime of the app process (from main via sync IPC). Preload
// runs again on every full navigation, but the ID must not change until quit —
// otherwise localStorage "confirmed launch" drifts and ServerUrlGuard re-prompts.
const launchId = ipcRenderer.sendSync('pipeshub-get-launch-id');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform: process.platform,
  launchId,
  /** Opens a native folder-picker dialog. Returns the selected path or null. */
  selectFolder: () => ipcRenderer.invoke('select-folder'),
  /**
   * Proxy a fetch through the main process. Used for SSE / streaming requests
   * that can't rely on the renderer's fetch (CORS on app:// origin).
   *
   * Callback-based API (Response/ReadableStream instances can't cross the
   * contextBridge boundary — their methods get stripped). The renderer
   * wraps these callbacks into a ReadableStream itself.
   *
   * Returns an `abort()` function.
   */
  streamFetch: (url, init, callbacks) => {
    const streamId = `s${Date.now()}-${nextStreamId++}`;
    const headers = (init && init.headers) || {};
    const body = init && init.body != null ? init.body : undefined;
    const method = (init && init.method) || 'GET';

    const onHeaders = (_e, data) => {
      if (data.streamId !== streamId) return;
      callbacks.onHeaders && callbacks.onHeaders({
        ok: data.ok,
        status: data.status,
        statusText: data.statusText,
        headers: data.headers,
      });
    };
    const onChunk = (_e, data) => {
      if (data.streamId !== streamId) return;
      callbacks.onChunk && callbacks.onChunk(new Uint8Array(data.chunk));
    };
    const onEnd = (_e, data) => {
      if (data.streamId !== streamId) return;
      cleanup();
      callbacks.onEnd && callbacks.onEnd();
    };
    const onError = (_e, data) => {
      if (data.streamId !== streamId) return;
      cleanup();
      callbacks.onError && callbacks.onError({ name: data.name, message: data.message });
    };
    const cleanup = () => {
      ipcRenderer.removeListener('stream/headers', onHeaders);
      ipcRenderer.removeListener('stream/chunk', onChunk);
      ipcRenderer.removeListener('stream/end', onEnd);
      ipcRenderer.removeListener('stream/error', onError);
    };

    ipcRenderer.on('stream/headers', onHeaders);
    ipcRenderer.on('stream/chunk', onChunk);
    ipcRenderer.on('stream/end', onEnd);
    ipcRenderer.on('stream/error', onError);

    ipcRenderer.invoke('stream/start', { streamId, url, method, headers, body });

    return () => ipcRenderer.send('stream/abort', { streamId });
  },
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
