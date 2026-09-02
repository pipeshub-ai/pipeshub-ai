import {
  app,
  BrowserWindow,
  protocol,
  net,
  nativeImage,
  session,
  ipcMain,
  dialog,
  shell,
  systemPreferences,
  safeStorage,
  type IpcMainInvokeEvent,
  type IpcMainEvent,
  type NativeImage,
} from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import {
  ContentStreamer,
  DesktopCredentialsStore,
  DesktopSocketClient,
  LocalSyncManager,
  type ConnectorStatus,
} from './local-sync';
import {
  openLocalFsRecordSource,
  type OpenLocalFsRecordSourcePayload,
} from './local-sync/open-record-source';

// Directory where `next build` (static export) output lands after electron:copy
// Static export lives at electron/out/ (see electron-prepare); main runs from electron/compile/
const STATIC_DIR = path.join(__dirname, '..', 'out');

// Custom protocol scheme — using a custom scheme ensures that root-relative
// paths like /_next/static/... resolve correctly against the export directory
// instead of the filesystem root (which is what happens with file://).
const SCHEME = 'app';

let mainWindow: BrowserWindow | null = null;
let localSyncManager: LocalSyncManager | null = null;
let desktopCredentials: DesktopCredentialsStore | null = null;
let desktopSocket: DesktopSocketClient | null = null;
let isQuitting = false;

// Single-instance lock so only one app instance runs watchers / dispatch.
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
}

function getAppIcon(): NativeImage | undefined {
  const logoDir = path.join(STATIC_DIR, 'logo');
  const candidates = [
    path.join(logoDir, 'pipes-hub-256.png'),
    path.join(logoDir, 'pipes-hub-512.png'),
    path.join(logoDir, 'pipes-hub-1024.png'),
  ];
  for (const pngPath of candidates) {
    if (fs.existsSync(pngPath)) {
      return nativeImage.createFromPath(pngPath);
    }
  }
  return undefined;
}

// Must be called before app.whenReady() to register the scheme as privileged
protocol.registerSchemesAsPrivileged([
  {
    scheme: SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
]);

function createWindow(): void {
  const icon = getAppIcon();

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 375,
    minHeight: 600,
    title: 'PipesHub',
    ...(icon ? { icon } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Load the static export entry point via the custom protocol.
  // Start at /chat/ — the existing guards handle all cases:
  //   • ServerUrlGuard: prompts for the API URL until acknowledged (pre-filled
  //     with the last saved value; editable); survives restarts
  //   • AuthGuard: redirects to /login if not authenticated
  //   • If already authenticated: renders chat immediately (no round-trip via login)
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (
        parsed.protocol === 'http:' ||
        parsed.protocol === 'https:' ||
        parsed.protocol === 'mailto:' ||
        parsed.protocol === 'tel:'
      ) {
        void shell.openExternal(url);
        return { action: 'deny' };
      }
    } catch {
      // ignore malformed URLs
    }
    return { action: 'deny' };
  });

  mainWindow.loadURL(`${SCHEME}://./chat/`);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

interface StreamStartPayload {
  streamId: string;
  url: string;
  method?: string;
  headers?: Record<string, string>;
  body?: string;
}

interface ConnectorIdPayload {
  connectorId?: string;
}

interface CredentialsPayload {
  refreshToken?: string;
  apiBaseUrl?: string;
}

app.whenReady().then(() => {
  // ── CORS bypass ──────────────────────────────────────────────────────────
  // The renderer runs under the app:// origin which the backend's CORS config
  // doesn't know about. Inject permissive CORS headers on every response so
  // that fetch / XMLHttpRequest from the renderer can reach the API server.
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers: Record<string, string | string[]> = { ...details.responseHeaders };
    headers['access-control-allow-origin'] = ['*'];
    headers['access-control-allow-headers'] = ['*'];
    headers['access-control-allow-methods'] = ['GET, POST, PUT, PATCH, DELETE, OPTIONS'];
    callback({ responseHeaders: headers });
  });

  // Mic / camera for chat voice (MediaRecorder + getUserMedia)
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    if (permission === 'media') {
      if (process.platform === 'darwin') {
        void systemPreferences.askForMediaAccess('microphone').then((granted) => {
          callback(granted);
        });
        return;
      }
      callback(true);
      return;
    }
    callback(true);
  });

  localSyncManager = new LocalSyncManager({
    app,
    onStatusChange: (status: ConnectorStatus) => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      mainWindow.webContents.send('local-sync-status', status);
    },
  });

  // Credentials live beside the journal so sync keeps working with the window
  // closed — moving the scheduler server-side buys nothing if the desktop can
  // only answer while a renderer is alive.
  desktopCredentials = new DesktopCredentialsStore(localSyncManager.baseDir, safeStorage);
  const contentStreamer = new ContentStreamer({
    getRootPath: (connectorId: string) => localSyncManager?.getRootPath(connectorId) ?? null,
  });
  desktopSocket = new DesktopSocketClient({
    credentials: desktopCredentials,
    listConnectorIds: () => localSyncManager?.listConnectorIds() ?? [],
    servePull: (request) =>
      localSyncManager
        ? localSyncManager.servePull(request)
        : Promise.resolve({
            ok: false as const,
            runId: request.runId,
            batchIndex: request.batchIndex,
            error: {
              code: 'INTERNAL' as const,
              message: 'Local sync is not initialized',
              retryable: true,
            },
          }),
    serveContent: (request, emitChunk, abort) =>
      contentStreamer.serve(request, emitChunk, abort),
    onRegistration: (ack) => {
      localSyncManager?.setForeignOwnedConnectors(
        ack.rejected.map((entry) => entry.connectorId),
      );
    },
  });

  // Handle the custom app:// protocol — map requests to static export files
  protocol.handle(SCHEME, (request) => {
    const url = new URL(request.url);
    const pathname = decodeURIComponent(url.pathname);

    // Resolve to a file inside the static export directory
    let filePath = path.join(STATIC_DIR, pathname);

    // If the path is a directory, serve index.html (Next.js trailingSlash output)
    if (filePath.endsWith('/') || filePath.endsWith(path.sep)) {
      filePath = path.join(filePath, 'index.html');
    }

    // If file doesn't exist and has no extension, try appending /index.html
    // (handles routes like /login -> /login/index.html)
    if (!path.extname(filePath) && !fs.existsSync(filePath)) {
      const withIndex = path.join(filePath, 'index.html');
      if (fs.existsSync(withIndex)) {
        filePath = withIndex;
      }
    }

    return net.fetch('file://' + filePath);
  });

  // Set the dock icon on macOS
  if (process.platform === 'darwin') {
    const icon = getAppIcon();
    if (icon && app.dock) app.dock.setIcon(icon);
  }

  // ── IPC handlers ─────────────────────────────────────────────────────────
  // Open a native folder picker dialog and return the selected path.
  ipcMain.handle('select-folder', async () => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });

  ipcMain.handle('local-sync/start', async (_event: IpcMainInvokeEvent, payload: Parameters<LocalSyncManager['start']>[0]) => {
    if (!localSyncManager) return null;
    const status = await localSyncManager.start(payload || ({} as Parameters<LocalSyncManager['start']>[0]));
    // Await the claim: toggle-on publishes an immediate pull as soon as this
    // IPC returns, and a fire-and-forget register loses that race.
    await desktopSocket?.register();
    return status;
  });

  ipcMain.handle('local-sync/check-root-path', async (_event: IpcMainInvokeEvent, payload: { connectorId: string; rootPath: string }) => {
    if (!localSyncManager || !payload?.connectorId || !payload?.rootPath) {
      return { available: true };
    }
    return localSyncManager.checkRootPathConflict(payload.connectorId, payload.rootPath);
  });

  ipcMain.handle('local-sync/stop', async (_event: IpcMainInvokeEvent, payload: ConnectorIdPayload) => {
    if (!localSyncManager || !payload?.connectorId) return null;
    const status = await localSyncManager.stop(payload.connectorId);
    // Release the claim so another machine can take the folder over without
    // waiting for this app to quit.
    desktopSocket?.unregister([payload.connectorId]);
    return status;
  });

  ipcMain.handle('local-sync/remove', async (_event: IpcMainInvokeEvent, payload: ConnectorIdPayload) => {
    if (!localSyncManager || !payload?.connectorId) return { ok: false };
    await localSyncManager.remove(payload.connectorId);
    desktopSocket?.unregister([payload.connectorId]);
    return { ok: true };
  });

  ipcMain.handle('local-sync/reap', async (_event: IpcMainInvokeEvent, payload?: { connectorIds?: string[] }) => {
    if (!localSyncManager || !Array.isArray(payload?.connectorIds)) return { removed: [] };
    const removed = await localSyncManager.reap(payload.connectorIds);
    if (removed.length > 0) desktopSocket?.unregister(removed);
    return { removed };
  });

  ipcMain.handle('local-sync/status', async (_event: IpcMainInvokeEvent, payload?: ConnectorIdPayload) => {
    if (!localSyncManager) return null;
    return localSyncManager.getStatus(payload?.connectorId);
  });

  ipcMain.handle('local-sync/bootstrap', async () => {
    if (!localSyncManager) return [];
    const results = await localSyncManager.bootstrapFromJournal();
    await desktopSocket?.register();
    return results;
  });

  // The renderer hands the refresh token over at login; from then on main
  // mints its own access tokens and no longer needs a window.
  ipcMain.handle('local-sync/credentials', async (_event: IpcMainInvokeEvent, payload: CredentialsPayload) => {
    if (!desktopCredentials || !payload?.refreshToken || !payload?.apiBaseUrl) {
      return { ok: false, error: 'refreshToken and apiBaseUrl are required' };
    }
    try {
      const result = desktopCredentials.setCredentials({
        refreshToken: payload.refreshToken,
        apiBaseUrl: payload.apiBaseUrl,
      });
      await desktopSocket?.reconnectWithNewCredential();
      return { ok: true, ...result };
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle('local-sync/clear-credentials', async () => {
    desktopCredentials?.clear();
    desktopSocket?.disconnect();
    return { ok: true };
  });

  ipcMain.handle('local-fs/open-record-source', async (_event: IpcMainInvokeEvent, payload: OpenLocalFsRecordSourcePayload) => {
    if (!localSyncManager) {
      return {
        ok: false,
        code: 'LOCAL_SYNC_UNAVAILABLE',
        error: 'Local sync is unavailable in this desktop session.',
      };
    }
    return openLocalFsRecordSource(payload || {}, {
      getMeta: (connectorId: string) => localSyncManager?.journal.getMeta(connectorId) ?? null,
      showItemInFolder: (targetPath: string) => shell.showItemInFolder(targetPath),
      openPath: (targetPath: string) => shell.openPath(targetPath),
    });
  });

  // ── Streaming fetch proxy ────────────────────────────────────────────────
  // The renderer runs under the app:// origin. Chromium enforces CORS on
  // fetch(), and in particular the ReadableStream returned by
  // `response.body.getReader()` is unreliable for long-lived SSE responses
  // from a cross-origin backend. Rather than disable webSecurity (unsafe),
  // we proxy streaming requests through the main process using Electron's
  // `net.fetch`, which has no CORS enforcement, and forward chunks to the
  // renderer over IPC. The renderer reconstructs a ReadableStream and feeds
  // it to the existing SSE parser unchanged.
  const activeStreams = new Map<string, AbortController>();

  ipcMain.handle('stream/start', async (event: IpcMainInvokeEvent, payload: StreamStartPayload) => {
    const { streamId, url, method, headers, body } = payload || ({} as StreamStartPayload);
    if (!streamId || !url) return;

    const controller = new AbortController();
    activeStreams.set(streamId, controller);

    const wc = event.sender;
    const send = (channel: string, data: unknown) => {
      if (!wc.isDestroyed()) wc.send(channel, data);
    };

    try {
      const response = await net.fetch(url, {
        method: method || 'GET',
        headers: headers || {},
        body: body != null ? body : undefined,
        signal: controller.signal,
      });

      send('stream/headers', {
        streamId,
        status: response.status,
        statusText: response.statusText,
        ok: response.ok,
        headers: Object.fromEntries(response.headers.entries()),
      });

      if (!response.body) {
        send('stream/end', { streamId });
        activeStreams.delete(streamId);
        return;
      }

      const reader = response.body.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // `value` is a Uint8Array — transferable over IPC as a plain buffer.
        send('stream/chunk', { streamId, chunk: value });
      }
      send('stream/end', { streamId });
    } catch (err) {
      const isAbort = err instanceof Error && (err.name === 'AbortError' || controller.signal.aborted);
      send('stream/error', {
        streamId,
        name: isAbort ? 'AbortError' : (err instanceof Error ? err.name : 'Error'),
        message: err instanceof Error ? err.message : 'Stream request failed',
      });
    } finally {
      activeStreams.delete(streamId);
    }
  });

  ipcMain.on('stream/abort', (_event: IpcMainEvent, payload: { streamId?: string }) => {
    const controller = payload?.streamId ? activeStreams.get(payload.streamId) : undefined;
    if (controller) controller.abort();
  });

  createWindow();
  // Mount watchers and connect before any window work: the server owns sync
  // cadence now, and a scheduled tick must land on a desktop that is answering
  // even with the window closed.
  localSyncManager
    .bootstrapFromJournal()
    .then(() => desktopSocket?.connect())
    .catch((error: unknown) => {
      console.warn('[local-sync] initialization failed:', error);
    });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// Drain local-sync watchers (flush pending dispatches, persist state) before exit.
app.on('before-quit', async (event) => {
  if (isQuitting || !localSyncManager) return;
  event.preventDefault();
  isQuitting = true;
  try {
    desktopSocket?.disconnect();
    await localSyncManager.shutdown();
  } catch (error) {
    console.warn('[local-sync] shutdown error:', error);
  }
  app.exit(0);
});
