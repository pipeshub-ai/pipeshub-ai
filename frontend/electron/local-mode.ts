import * as path from 'path';
import type {
  App,
  BrowserWindow,
  IpcMain,
  IpcMainInvokeEvent,
  NativeImage,
  Shell,
  Tray as TrayType,
  Menu as MenuType,
} from 'electron';
import { nativeImage } from 'electron';
import type { DesktopCredentialsStore } from './local-sync';
import { LauncherClient, type LocalServerStatus } from './local-server/launcher-client';
import { resolveLauncher } from './local-server/launcher-path';
import { DesktopSettingsStore, type DesktopMode } from './local-server/desktop-settings';
import { indexRepository, LocalRepoApiError, LocalRepoClient, type RepoStatus } from './local-repos/client';
import { LocalRepoStore } from './local-repos/store';

export interface LocalModeDeps {
  app: App;
  ipcMain: IpcMain;
  shell: Shell;
  Tray: typeof TrayType;
  Menu: typeof MenuType;
  getWindow: () => BrowserWindow | null;
  getIcon: () => NativeImage | undefined;
  getCredentials: () => DesktopCredentialsStore | null;
  /** Drop the token held for the local server and close the socket to it: the server is gone and its port is free. */
  forgetCredentials?: () => void;
  /** Re-opens the main window (tray "Open"). */
  showWindow: () => void;
  env?: NodeJS.ProcessEnv;
}

/**
 * "Run PipesHub on this computer": owns the launcher process, the user's mode
 * choice and the tray. The renderer reaches it over the `desktop/*` and
 * `local-repo/*` IPC channels registered here.
 */
export class LocalMode {
  readonly settings: DesktopSettingsStore;
  private readonly launcher: LauncherClient | null;
  private readonly dataDir: string;
  private readonly repos: LocalRepoStore;
  private tray: TrayType | null = null;
  /** True once this process started (or adopted) a server, so only then does quitting stop one. */
  private engaged = false;

  constructor(private readonly deps: LocalModeDeps) {
    const userData = deps.app.getPath('userData');
    this.settings = new DesktopSettingsStore(path.join(userData, 'desktop-settings.json'));
    this.repos = new LocalRepoStore(path.join(userData, 'local-repos.json'));
    // Data lives in the OS app-data folder, never in the (read-only) app bundle.
    this.dataDir = path.join(userData, 'local-server');
    const env = deps.env ?? process.env;
    const resolved = resolveLauncher({
      isPackaged: deps.app.isPackaged,
      resourcesPath: process.resourcesPath,
      platform: process.platform,
      env,
      devBundleDir: path.join(__dirname, '..', '..', 'resources'),
    });
    this.launcher = resolved
      ? new LauncherClient({
          command: resolved.command,
          args: resolved.args,
          dataDir: this.dataDir,
          extraEnv: resolved.bundleDir ? { PIPESHUB_BUNDLE: resolved.bundleDir, PYTHONDONTWRITEBYTECODE: '1' } : {},
        })
      : null;
    this.launcher?.on('status', (status: { state?: string; url?: string }) => {
      this.forgetTokenWhenServerGone(status);
      this.emit();
    });
  }

  private lastRunningOrigin: string | null = null;

  /**
   * Once this launch's server stops or crashes its port is free, and the socket would keep reconnecting there with the
   * token. Main forgets a token held for that origin; the renderer pushes it again for the server's next URL.
   */
  private forgetTokenWhenServerGone(status: { state?: string; url?: string }): void {
    const origin = (u: string | null | undefined): string | null => {
      try { return u ? new URL(u).origin : null; } catch { return null; }
    };
    if (status.state === 'running') {
      this.lastRunningOrigin = origin(status.url);
      return;
    }
    if (this.lastRunningOrigin && origin(this.deps.getCredentials()?.apiBaseUrl) === this.lastRunningOrigin) {
      this.deps.forgetCredentials?.();
    }
    this.lastRunningOrigin = null;
  }

  /** Pushes the whole state; the renderer replaces its copy. */
  private emit(): void {
    const win = this.deps.getWindow();
    if (win && !win.isDestroyed()) win.webContents.send('desktop/state-changed', this.state());
  }

  private state() {
    return {
      ...this.settings.get(),
      localAvailable: this.launcher !== null,
      local: this.launcher?.status ?? ({ state: 'idle' } as LocalServerStatus),
      dataDir: this.dataDir,
    };
  }

  /** Start at launch when the user already chose local mode. */
  autoStart(): void {
    if (this.settings.get().mode === 'local') void this.start();
  }

  private start(): Promise<LocalServerStatus> {
    if (!this.launcher) {
      return Promise.resolve({
        state: 'failed',
        error: { code: 'launcher_missing', message: 'This build does not include the local PipesHub server.', logTail: [] },
      } as LocalServerStatus);
    }
    this.engaged = true;
    return this.launcher.start();
  }

  /** Quit path: stop the server we started. Bounded by the launcher client's own timeouts. */
  async shutdown(): Promise<void> {
    if (this.engaged) await this.launcher?.stop();
    this.tray?.destroy();
    this.tray = null;
  }

  /** Window close in local mode with "keep running" on: hide to the tray instead. */
  hideInsteadOfClose(): boolean {
    const s = this.settings.get();
    if (s.mode !== 'local' || !s.keepRunningInTray || this.launcher?.status.state !== 'running') return false;
    this.ensureTray();
    return true;
  }

  private ensureTray(): void {
    if (this.tray) return;
    const { Tray, Menu, app } = this.deps;
    const icon = this.deps.getIcon()?.resize({ width: 18, height: 18 });
    this.tray = new Tray(icon ?? nativeImage.createEmpty());
    this.tray.setToolTip('PipesHub (running on this computer)');
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: 'Open PipesHub', click: () => this.deps.showWindow() },
        { type: 'separator' },
        { label: 'Quit PipesHub', click: () => app.quit() },
      ]),
    );
    this.tray.on('click', () => this.deps.showWindow());
  }

  private localRepoContext(): { client: LocalRepoClient } {
    const status = this.launcher?.status;
    const creds = this.deps.getCredentials();
    const origin = (u: string | null | undefined): string | null => {
      try {
        return u ? new URL(u).origin : null;
      } catch {
        return null;
      }
    };
    // Never send a repository to some other server because the user is signed in there.
    if (status?.state !== 'running' || !creds?.hasCredential() || origin(creds.apiBaseUrl) !== origin(status.url)) {
      throw new LocalRepoApiError(409, 'LOCAL_MODE_REQUIRED: sign in to PipesHub running on this computer first.');
    }
    return { client: new LocalRepoClient(status.url as string, () => creds.getAccessToken()) };
  }

  register(): void {
    const { ipcMain, shell } = this.deps;
    ipcMain.handle('desktop/state', () => this.state());

    ipcMain.handle('desktop/set-mode', async (_e: IpcMainInvokeEvent, payload: { mode?: DesktopMode }) => {
      const mode = payload?.mode;
      if (mode !== 'local' && mode !== 'remote') return this.state();
      this.settings.update({ mode });
      if (mode === 'local') void this.start();
      else if (this.engaged) await this.launcher?.stop();
      this.emit();
      return this.state();
    });

    ipcMain.handle('desktop/set-keep-running', (_e: IpcMainInvokeEvent, payload: { value?: boolean }) => {
      this.settings.update({ keepRunningInTray: payload?.value === true });
      this.emit();
      return this.state();
    });

    ipcMain.handle('desktop/local-retry', async () => {
      await this.start();
      return this.state();
    });

    ipcMain.handle('desktop/local-open-logs', () => shell.openPath(path.join(this.dataDir, 'logs')));

    ipcMain.handle('local-repo/add', async (_e: IpcMainInvokeEvent, payload: { rootPath?: string }) => {
      try {
        const rootPath = payload?.rootPath;
        if (!rootPath || !path.isAbsolute(rootPath)) return { ok: false, error: 'An absolute folder path is required.' };
        const { client } = this.localRepoContext();
        const entry = this.repos.ensure(rootPath);
        const result = await indexRepository(client, entry.rootPath, entry.repoKey, entry.name);
        return { ok: true, repo: { ...entry, state: result.status.state }, uploaded: result.uploaded, skipped: result.skipped };
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) };
      }
    });

    ipcMain.handle('local-repo/list', async () => {
      const stored = this.repos.list();
      let remote: RepoStatus[] = [];
      try {
        remote = await this.localRepoContext().client.list();
      } catch {
        /* server not ready or signed out: show what we know */
      }
      const byKey = new Map(remote.map((r) => [r.repoKey, r]));
      return stored.map((e) => ({ ...e, state: byKey.get(e.repoKey)?.state ?? 'unknown', files: byKey.get(e.repoKey)?.files ?? 0 }));
    });

    ipcMain.handle('local-repo/remove', async (_e: IpcMainInvokeEvent, payload: { repoKey?: string }) => {
      try {
        const key = payload?.repoKey;
        if (!key || !this.repos.findByKey(key)) return { ok: false, error: 'Unknown repository.' };
        await this.localRepoContext().client.remove(key);
        this.repos.remove(key);
        return { ok: true };
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) };
      }
    });
  }
}
