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
  type IpcMainInvokeEvent,
  type IpcMainEvent,
  type NativeImage,
} from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { pathToFileURL } from 'url';
import {
  ContentStreamer,
  DesktopCredentialsStore,
  DesktopSocketClient,
  LocalSyncManager,
  resolveDeviceIdentity,
  type ConnectorStatus,
} from './local-sync';
import {
  openLocalFsRecordSource,
  type OpenLocalFsRecordSourcePayload,
} from './local-sync/open-record-source';
import {
  DEEP_LINK_SCHEME,
  findDeepLinkInArgv,
  parseOAuthDeepLink,
  type OAuthDeepLink,
} from './deep-link';

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
let deviceIdentityReady: Promise<void> = Promise.resolve();
let deviceIdentityError: string | null = null;
let isQuitting = false;
/** Last OAuth deep link, held until a renderer subscriber takes it (see handleDeepLinkUrl). */
let pendingDeepLink: (OAuthDeepLink & { receivedAt: number }) | null = null;

// Sign-in runs in the user's default browser because no provider accepts an
// app:// redirect URI. Registering this scheme is how the result gets back.
if (process.defaultApp && process.argv.length >= 2) {
  // Running from source: argv[0] is electron itself, so the entry path has to be
  // registered with it or the OS launches a bare Electron shell instead of us.
  app.setAsDefaultProtocolClient(DEEP_LINK_SCHEME, process.execPath, [
    path.resolve(process.argv[1]),
  ]);
} else {
  app.setAsDefaultProtocolClient(DEEP_LINK_SCHEME);
}

/** A link older than this is stale; a sign-in attempt has long since timed out. */
const DEEP_LINK_TTL_MS = 5 * 60 * 1000;

function focusMainWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
}

function deliverDeepLink(link: OAuthDeepLink & { receivedAt: number }): void {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('oauth/callback', link);
}

/**
 * Buffer as well as send: on a cold start the window exists well before React
 * mounts a subscriber, and a send with no listener is simply dropped. The
 * renderer drains the buffer when it subscribes, and consumes single-use, so
 * receiving the same link both ways is harmless.
 *
 * Never log rawUrl — it carries the id_token or auth code.
 */
function handleDeepLinkUrl(rawUrl: string | null | undefined): void {
  if (!rawUrl) return;
  const parsed = parseOAuthDeepLink(rawUrl);
  if (!parsed) return;

  pendingDeepLink = { ...parsed, receivedAt: Date.now() };
  focusMainWindow();
  deliverDeepLink(pendingDeepLink);
}

// macOS delivers deep links as an event, and can do so before the app is ready,
// so this has to be registered at module scope rather than inside whenReady().
app.on('open-url', (event, url) => {
  event.preventDefault();
  handleDeepLinkUrl(url);
});

// Single-instance lock so only one app instance runs watchers / dispatch.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    focusMainWindow();
    // Windows and Linux hand the link to the second instance as an argument.
    handleDeepLinkUrl(findDeepLinkInArgv(argv));
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

interface AccessTokenPayload {
  accessToken?: string;
  apiBaseUrl?: string;
}

/**
 * Handlers that talk to the server wait for identity resolution instead of
 * reading a null socket, since the renderer can invoke them before it lands.
 */
async function requireDesktopSocket(): Promise<{
  credentials: DesktopCredentialsStore;
  socket: DesktopSocketClient;
}> {
  await deviceIdentityReady;
  if (!desktopCredentials || !desktopSocket) {
    throw new Error(
      `DEVICE_IDENTITY_UNAVAILABLE: ${deviceIdentityError ?? 'device identity has not been resolved'}`,
    );
  }
  return { credentials: desktopCredentials, socket: desktopSocket };
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

  const contentStreamer = new ContentStreamer({
    getRootPath: (connectorId: string) => localSyncManager?.getRootPath(connectorId) ?? null,
  });
  // Without a machine id the server cannot tell this desktop from another, so
  // the socket is never built; the window still opens so the error can surface.
  deviceIdentityReady = resolveDeviceIdentity().then(
    (identity) => {
      desktopCredentials = new DesktopCredentialsStore(identity);
      desktopSocket = new DesktopSocketClient({
        credentials: desktopCredentials,
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
      });
    },
    (error: unknown) => {
      deviceIdentityError = error instanceof Error ? error.message : String(error);
      console.error('[local-sync] device identity unavailable; local sync is disabled:', error);
    },
  );

  // Handle the custom app:// protocol — map requests to static export files
  protocol.handle(SCHEME, (request) => {
    const url = new URL(request.url);
    const pathname = decodeURIComponent(url.pathname);

    // Resolve to a file inside the static export directory
    let filePath = path.join(STATIC_DIR, pathname);

    // An extensionless route resolves to the directory the export wrote it as
    // (/login -> out/login), whether or not it carries a trailing slash.
    // Fetching the directory itself fails with ERR_UNEXPECTED, so prefer the
    // index.html inside it -- this is what a full navigation to a route hits.
    if (!path.extname(filePath)) {
      const withIndex = path.join(filePath, 'index.html');
      if (fs.existsSync(withIndex)) {
        filePath = withIndex;
      }
    }

    // pathToFileURL, not string concatenation: the install path can contain
    // spaces and a bare drive letter is not a valid file:// host.
    return net.fetch(pathToFileURL(filePath).toString());
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

  ipcMain.handle('local-sync/device-info', async () => {
    await deviceIdentityReady;
    if (!desktopCredentials) {
      return { ok: false, error: deviceIdentityError ?? 'device identity has not been resolved' };
    }
    return {
      ok: true,
      deviceId: desktopCredentials.deviceId,
      deviceName: desktopCredentials.deviceName,
    };
  });

  ipcMain.handle('local-sync/start', async (_event: IpcMainInvokeEvent, payload: Parameters<LocalSyncManager['start']>[0]) => {
    if (!localSyncManager) return null;
    const { socket } = await requireDesktopSocket();
    const status = await localSyncManager.start(payload || ({} as Parameters<LocalSyncManager['start']>[0]));
    // Await the registration: toggle-on publishes an immediate pull as soon as
    // this IPC returns, and a fire-and-forget register loses that race.
    await socket.register();
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
    return localSyncManager.stop(payload.connectorId);
  });

  ipcMain.handle('local-sync/remove', async (_event: IpcMainInvokeEvent, payload: ConnectorIdPayload) => {
    if (!localSyncManager || !payload?.connectorId) return { ok: false };
    await localSyncManager.remove(payload.connectorId);
    return { ok: true };
  });

  ipcMain.handle('local-sync/reap', async (_event: IpcMainInvokeEvent, payload?: { connectorIds?: string[] }) => {
    if (!localSyncManager || !Array.isArray(payload?.connectorIds)) return { removed: [] };
    const removed = await localSyncManager.reap(payload.connectorIds);
    return { removed };
  });

  ipcMain.handle('local-sync/status', async (_event: IpcMainInvokeEvent, payload?: ConnectorIdPayload) => {
    if (!localSyncManager) return null;
    return localSyncManager.getStatus(payload?.connectorId);
  });

  ipcMain.handle('local-sync/bootstrap', async () => {
    if (!localSyncManager) return [];
    const { socket } = await requireDesktopSocket();
    const results = await localSyncManager.bootstrapFromJournal();
    await socket.register();
    return results;
  });

  // The renderer pushes its access token at login and on every refresh; main
  // never mints one, so sync runs as long as this process holds a live token.
  ipcMain.handle('local-sync/access-token', async (_event: IpcMainInvokeEvent, payload: AccessTokenPayload) => {
    if (!payload?.accessToken || !payload?.apiBaseUrl) {
      return { ok: false, error: 'accessToken and apiBaseUrl are required' };
    }
    try {
      const { credentials, socket } = await requireDesktopSocket();
      const { deviceId, changed } = credentials.setAccessToken({
        accessToken: payload.accessToken,
        apiBaseUrl: payload.apiBaseUrl,
      });
      // A re-push of the token already in hand must not tear down a healthy
      // socket; the renderer pushes on every store change, not only on refresh.
      if (changed || !socket.connected) {
        await socket.reconnectWithNewCredential();
      }
      return { ok: true, deviceId };
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

  // The renderer cannot open a browser window itself — setWindowOpenHandler
  // denies every popup. https-only so that a renderer-side injection cannot
  // turn this into "launch any URL, in any scheme, as a trusted local app".
  ipcMain.handle('oauth/open-external', async (_event: IpcMainInvokeEvent, payload: { url?: string }) => {
    const url = payload?.url;
    if (!url) return { ok: false, error: 'No URL supplied.' };
    try {
      if (new URL(url).protocol !== 'https:') {
        return { ok: false, error: 'Only https URLs can be opened externally.' };
      }
    } catch {
      return { ok: false, error: 'Malformed URL.' };
    }
    await shell.openExternal(url);
    return { ok: true };
  });

  /**
   * Redeem an OAuth authorization code from the main process.
   *
   * Microsoft redeems a single-page-application code only cross-origin
   * (AADSTS9002327), so the request has to carry the Origin that app
   * registration lists. The renderer cannot supply it — its origin is app://
   * and fetch refuses to let a caller override Origin — and net.fetch strips it
   * as a forbidden header, so this goes over net.request where it is ours to set.
   */
  ipcMain.handle('oauth/token-exchange', async (
    _event: IpcMainInvokeEvent,
    payload: { url?: string; body?: string; origin?: string },
  ) => {
    const { url, body, origin } = payload || {};
    if (!url || !body) return { ok: false, error: 'url and body are required.' };
    try {
      if (new URL(url).protocol !== 'https:') {
        return { ok: false, error: 'Only https token endpoints can be used.' };
      }
      if (origin) {
        const originProtocol = new URL(origin).protocol;
        if (originProtocol !== 'https:' && originProtocol !== 'http:') {
          return { ok: false, error: 'Origin must be an http or https URL.' };
        }
      }
    } catch {
      return { ok: false, error: 'Malformed URL.' };
    }

    return new Promise((resolve) => {
      const request = net.request({ method: 'POST', url });
      request.setHeader('Content-Type', 'application/x-www-form-urlencoded');
      if (origin) request.setHeader('Origin', origin);

      request.on('response', (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(Buffer.from(chunk)));
        response.on('end', () => {
          resolve({
            ok: true,
            status: response.statusCode,
            body: Buffer.concat(chunks).toString('utf8'),
          });
        });
      });
      // Never include the body in an error: it carries the code and the tokens.
      request.on('error', (error: Error) => resolve({ ok: false, error: error.message }));
      request.write(body);
      request.end();
    });
  });

  ipcMain.handle('oauth/pending', () => {
    const link = pendingDeepLink;
    pendingDeepLink = null;
    if (!link) return null;
    return Date.now() - link.receivedAt > DEEP_LINK_TTL_MS ? null : link;
  });

  ipcMain.on('stream/abort', (_event: IpcMainEvent, payload: { streamId?: string }) => {
    const controller = payload?.streamId ? activeStreams.get(payload.streamId) : undefined;
    if (controller) controller.abort();
  });

  createWindow();
  // Cold start from a deep link: it arrives in argv, not through an event.
  handleDeepLinkUrl(findDeepLinkInArgv(process.argv));
  // A reload drops any listener the renderer had registered; re-send so a link
  // that landed during startup is not lost.
  mainWindow?.webContents.on('did-finish-load', () => {
    if (pendingDeepLink) deliverDeepLink(pendingDeepLink);
  });
  // Mount watchers up front so the journal is warm by the time the renderer
  // pushes a token; connect() no-ops until then.
  deviceIdentityReady
    .then(async () => {
      if (!localSyncManager || !desktopSocket) return;
      await localSyncManager.bootstrapFromJournal();
      await desktopSocket.connect();
    })
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
