const { app, BrowserWindow, protocol, net, nativeImage, session, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { LocalSyncManager } = require('./local-sync/manager');

// Directory where `next build` (static export) output lands after electron:copy
const STATIC_DIR = path.join(__dirname, 'out');

// Custom protocol scheme — using a custom scheme ensures that root-relative
// paths like /_next/static/... resolve correctly against the export directory
// instead of the filesystem root (which is what happens with file://).
const SCHEME = 'app';

let mainWindow;
let localSyncManager;
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

function getAppIcon() {
  const pngPath = path.join(STATIC_DIR, 'logo', 'pipes-hub-1024.png');
  if (fs.existsSync(pngPath)) {
    return nativeImage.createFromPath(pngPath);
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

function createWindow() {
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
  //   • ServerUrlGuard: shows setup screen if no API URL is stored yet
  //   • AuthGuard: redirects to /login if not authenticated
  //   • If already authenticated: renders chat immediately (no round-trip via login)
  mainWindow.loadURL(`${SCHEME}://./chat/`);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  // ── CORS bypass ──────────────────────────────────────────────────────────
  // The renderer runs under the app:// origin which the backend's CORS config
  // doesn't know about. Inject permissive CORS headers on every response so
  // that fetch / XMLHttpRequest from the renderer can reach the API server.
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders };
    headers['access-control-allow-origin'] = ['*'];
    headers['access-control-allow-headers'] = ['*'];
    headers['access-control-allow-methods'] = ['GET, POST, PUT, PATCH, DELETE, OPTIONS'];
    callback({ responseHeaders: headers });
  });

  localSyncManager = new LocalSyncManager({
    app,
    onStatusChange: (status) => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      mainWindow.webContents.send('local-sync-status', status);
    },
  });

  // Handle the custom app:// protocol — map requests to static export files
  protocol.handle(SCHEME, (request) => {
    const url = new URL(request.url);
    let pathname = decodeURIComponent(url.pathname);

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
    if (icon) app.dock.setIcon(icon);
  }

  // ── IPC handlers ─────────────────────────────────────────────────────────
  // Open a native folder picker dialog and return the selected path.
  ipcMain.handle('select-folder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });

  ipcMain.handle('local-sync/start', async (_event, payload) => {
    return localSyncManager.start(payload || {});
  });

  ipcMain.handle('local-sync/stop', async (_event, payload) => {
    return localSyncManager.stop(payload?.connectorId);
  });

  ipcMain.handle('local-sync/status', async (_event, payload) => {
    return localSyncManager.getStatus(payload?.connectorId);
  });

  ipcMain.handle('local-sync/rescan', async (_event, payload) => {
    if (!payload || !payload.connectorId) return null;
    return localSyncManager.rescan(payload.connectorId);
  });

  ipcMain.handle('local-sync/set-schedule', async (_event, payload) => {
    if (!payload || !payload.connectorId) return null;
    return localSyncManager.setSchedule(payload.connectorId, {
      syncStrategy: payload.syncStrategy,
      scheduledConfig: payload.scheduledConfig,
      connectorDisplayType: payload.connectorDisplayType,
    });
  });

  ipcMain.handle('local-sync/full-resync', async (_event, payload) => {
    if (!payload || !payload.connectorId) return null;
    try {
      const result = await localSyncManager.fullResync(payload.connectorId);
      return { ok: true, ...result, status: localSyncManager.getStatus(payload.connectorId) };
    } catch (error) {
      return {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
        status: localSyncManager.getStatus(payload.connectorId),
      };
    }
  });

  ipcMain.handle('local-sync/replay', async (_event, payload) => {
    if (payload?.connectorId) {
      return localSyncManager.replay(payload.connectorId);
    }
    const connectorIds = localSyncManager.journal.listConnectorIds();
    const results = [];
    for (const connectorId of connectorIds) {
      results.push(await localSyncManager.replay(connectorId));
    }
    return results;
  });

  createWindow();
  localSyncManager.init().catch((error) => {
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
    await localSyncManager.shutdown();
  } catch (error) {
    console.warn('[local-sync] shutdown error:', error);
  }
  app.exit(0);
});
