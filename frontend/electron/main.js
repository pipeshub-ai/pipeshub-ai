const { app, BrowserWindow, protocol, net, nativeImage, session, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');

// Directory where `next build` (static export) output lands after electron:copy
const STATIC_DIR = path.join(__dirname, 'out');

// Custom protocol scheme — using a custom scheme ensures that root-relative
// paths like /_next/static/... resolve correctly against the export directory
// instead of the filesystem root (which is what happens with file://).
const SCHEME = 'app';

let mainWindow;

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

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
