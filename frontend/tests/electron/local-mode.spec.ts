import { test, expect, _electron as electron, type ElectronApplication } from '@playwright/test';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

const ELECTRON_DIR = path.resolve(__dirname, '..', '..', 'electron');
const STUB = path.join(ELECTRON_DIR, 'compile', '__tests__', 'stub-launcher.js');

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function launch(userData: string, mode = 'ok'): Promise<ElectronApplication> {
  return electron.launch({
    args: [ELECTRON_DIR, '--no-sandbox', `--user-data-dir=${userData}`],
    env: {
      ...process.env,
      // The app registers the pipeshub:// handler; keep that out of the real home folder.
      HOME: path.join(userData, 'home'),
      PIPESHUB_LAUNCHER: process.execPath,
      PIPESHUB_LAUNCHER_ARGS: JSON.stringify([STUB]),
      STUB_MODE: mode,
    },
  });
}

async function quit(app: ElectronApplication): Promise<void> {
  const exited = new Promise<void>((resolve) => app.process().once('exit', () => resolve()));
  // `electron` types resolve to the local electron/ folder here, so the module is typed loosely.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await app.evaluate((electronModule) => (electronModule as any).app.quit());
  await exited;
}

test.describe('desktop local mode (stub launcher)', () => {
  let userData: string;
  const dataDir = () => path.join(userData, 'local-server');
  const read = (name: string) => fs.readFileSync(path.join(dataDir(), name), 'utf8');

  test.beforeEach(() => {
    userData = fs.mkdtempSync(path.join(os.tmpdir(), 'ph-e2e-'));
    fs.mkdirSync(dataDir(), { recursive: true });
  });
  test.afterEach(() => fs.rmSync(userData, { recursive: true, force: true }));

  test('first run -> local mode -> UI loads -> quit stops the server', async () => {
    const app = await launch(userData);
    const page = await app.firstWindow();

    // First run: the choice.
    await expect(page.getByTestId('mode-local')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('mode-remote')).toBeVisible();
    await page.getByTestId('mode-local').click();

    // The app UI (app://) is shown once the server is healthy, and talks to the loopback URL.
    await expect(page.getByTestId('mode-local')).toBeHidden({ timeout: 60_000 });
    await expect.poll(() => fs.existsSync(path.join(dataDir(), 'stub.log')) && /req GET \/api\//.test(read('stub.log')), { timeout: 60_000 }).toBe(true);
    expect(page.url().startsWith('app://')).toBe(true);
    // The real UI reached its sign-in route against this launch's server (the stub saw the request)...
    await expect.poll(() => page.url(), { timeout: 30_000 }).toMatch(/^app:\/\/.*\/login\/?$/);
    expect(read('stub.log')).toMatch(/req GET \/api\/v1\/org\/exists/);
    // ...whose URL is held in memory for this launch only: the port changes at every start, so it is never saved.
    const saved = await page.evaluate(() => [window.localStorage.getItem('PIPESHUB_API_BASE_URL'), window.sessionStorage.getItem('PIPESHUB_API_BASE_URL_SESSION')]);
    expect(saved).toEqual([null, null]);
    if (process.env.PH_E2E_SHOT) await page.screenshot({ path: process.env.PH_E2E_SHOT });

    const pid = Number(read('stub.pid'));
    expect(alive(pid)).toBe(true);

    await quit(app);
    expect(alive(pid)).toBe(false);
    expect(read('stub.log')).toMatch(/^stop$/m);
    expect(fs.existsSync(path.join(dataDir(), 'stub.pid'))).toBe(false);
  });

  test('the choice is remembered: next launch starts the server without asking', async () => {
    const first = await launch(userData);
    const p1 = await first.firstWindow();
    await p1.getByTestId('mode-local').click({ timeout: 60_000 });
    await expect.poll(() => fs.existsSync(path.join(dataDir(), 'stub.pid')), { timeout: 60_000 }).toBe(true);
    await quit(first);

    fs.writeFileSync(path.join(dataDir(), 'stub.log'), '');
    const second = await launch(userData);
    const p2 = await second.firstWindow();
    await expect.poll(() => fs.existsSync(path.join(dataDir(), 'stub.pid')), { timeout: 60_000 }).toBe(true);
    await expect(p2.getByTestId('mode-local')).toHaveCount(0);
    await quit(second);
  });

  test('a crash while starting shows the error with details, and Retry recovers', async () => {
    const app = await launch(userData, 'crash_before_ready');
    const page = await app.firstWindow();
    await page.getByTestId('mode-local').click({ timeout: 60_000 });
    await expect(page.getByTestId('local-retry')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('local-error-details')).toContainText('could not write lock file');

    fs.writeFileSync(path.join(dataDir(), 'stub-mode'), 'ok');
    await page.getByTestId('local-retry').click();
    await expect(page.getByTestId('local-retry')).toBeHidden({ timeout: 60_000 });
    await expect.poll(() => fs.existsSync(path.join(dataDir(), 'stub.pid')), { timeout: 30_000 }).toBe(true);
    const pid = Number(read('stub.pid'));
    await quit(app);
    expect(alive(pid)).toBe(false);
  });

  test('a port conflict is explained', async () => {
    const app = await launch(userData, 'port_in_use');
    const page = await app.firstWindow();
    await page.getByTestId('mode-local').click({ timeout: 60_000 });
    await expect(page.getByText(/already in use by another program/)).toBeVisible({ timeout: 60_000 });
    await quit(app);
  });

  test('without a launcher bundle the local option is disabled and server mode still works', async () => {
    test.skip(
      fs.existsSync(path.resolve(ELECTRON_DIR, '..', 'resources', 'pipeshub-native', 'launcher', 'src', 'main.ts')),
      'a launcher bundle is staged in resources/',
    );
    const env = { ...process.env, HOME: path.join(userData, 'home') } as Record<string, string>;
    delete env.PIPESHUB_LAUNCHER;
    const app = await electron.launch({ args: [ELECTRON_DIR, '--no-sandbox', `--user-data-dir=${userData}`], env });
    const page = await app.firstWindow();
    await expect(page.getByTestId('mode-local')).toBeDisabled({ timeout: 60_000 });
    await page.getByTestId('mode-remote').click();
    await expect(page.locator('#server-url')).toBeVisible();
    await quit(app);
  });

  // Local check of a built app (PIPESHUB_E2E_PACKAGED=<path to the packaged executable>): the
  // launcher is found under the app's resources, with no test override in the environment.
  test('packaged app starts the launcher bundled in its resources', async () => {
    const exe = process.env.PIPESHUB_E2E_PACKAGED;
    test.skip(!exe, 'set PIPESHUB_E2E_PACKAGED to a packaged app executable');
    const env = { ...process.env, HOME: path.join(userData, 'home') } as Record<string, string>;
    delete env.PIPESHUB_LAUNCHER;
    const app = await electron.launch({ executablePath: exe, args: [`--user-data-dir=${userData}`], env });
    const page = await app.firstWindow();
    await page.getByTestId('mode-local').click({ timeout: 60_000 });
    await expect.poll(() => fs.existsSync(path.join(dataDir(), 'stub.pid')), { timeout: 60_000 }).toBe(true);
    const pid = Number(read('stub.pid'));
    await quit(app);
    expect(alive(pid)).toBe(false);
  });
});
