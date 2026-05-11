import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

// Expose a minimal API to the renderer so it can detect Electron
// without needing nodeIntegration.
let nextStreamId = 1;

interface StreamFetchInit {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
}

interface StreamHeadersInfo {
  ok: boolean;
  status: number;
  statusText: string;
  headers: Record<string, string>;
}

interface StreamFetchCallbacks {
  onHeaders?: (info: StreamHeadersInfo) => void;
  onChunk?: (chunk: Uint8Array) => void;
  onEnd?: () => void;
  onError?: (err: { name: string; message: string }) => void;
}

interface StreamHeadersIpc { streamId: string; ok: boolean; status: number; statusText: string; headers: Record<string, string>; }
interface StreamChunkIpc { streamId: string; chunk: ArrayBufferLike; }
interface StreamEndIpc { streamId: string; }
interface StreamErrorIpc { streamId: string; name: string; message: string; }

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform: process.platform,
  /** Opens a native folder-picker dialog. Returns the selected path or null. */
  selectFolder: (): Promise<string | null> => ipcRenderer.invoke('select-folder'),
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
  streamFetch: (url: string, init: StreamFetchInit, callbacks: StreamFetchCallbacks): (() => void) => {
    const streamId = `s${Date.now()}-${nextStreamId++}`;
    const headers = (init && init.headers) || {};
    const body = init && init.body != null ? init.body : undefined;
    const method = (init && init.method) || 'GET';

    const onHeaders = (_e: IpcRendererEvent, data: StreamHeadersIpc) => {
      if (data.streamId !== streamId) return;
      callbacks.onHeaders && callbacks.onHeaders({
        ok: data.ok,
        status: data.status,
        statusText: data.statusText,
        headers: data.headers,
      });
    };
    const onChunk = (_e: IpcRendererEvent, data: StreamChunkIpc) => {
      if (data.streamId !== streamId) return;
      callbacks.onChunk && callbacks.onChunk(new Uint8Array(data.chunk));
    };
    const onEnd = (_e: IpcRendererEvent, data: StreamEndIpc) => {
      if (data.streamId !== streamId) return;
      cleanup();
      callbacks.onEnd && callbacks.onEnd();
    };
    const onError = (_e: IpcRendererEvent, data: StreamErrorIpc) => {
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
    start: (payload: unknown) => ipcRenderer.invoke('local-sync/start', payload),
    stop: (connectorId: string) => ipcRenderer.invoke('local-sync/stop', { connectorId }),
    status: (connectorId?: string) => ipcRenderer.invoke('local-sync/status', { connectorId }),
    replay: (connectorId?: string) => ipcRenderer.invoke('local-sync/replay', { connectorId }),
    fullResync: (connectorId: string) => ipcRenderer.invoke('local-sync/full-resync', { connectorId }),
    onStatus: (callback: (payload: unknown) => void): (() => void) => {
      const listener = (_event: IpcRendererEvent, payload: unknown) => callback(payload);
      ipcRenderer.on('local-sync-status', listener);
      return () => ipcRenderer.removeListener('local-sync-status', listener);
    },
  },
});
