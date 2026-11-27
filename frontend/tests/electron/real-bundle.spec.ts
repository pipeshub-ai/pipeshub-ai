import { test, expect, _electron as electron, type ElectronApplication, type Page } from '@playwright/test';
import { execFileSync, spawnSync } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

/**
 * The same desktop flows against the REAL native bundle (not the stub).
 * Opt-in: PIPESHUB_E2E_BUNDLE=<a native bundle folder: launcher/, node/, pg/, py/, app/>.
 * A cold start into a fresh data folder takes 25-100 s, so timeouts are long and tests share one data folder.
 */
const BUNDLE = process.env.PIPESHUB_E2E_BUNDLE;
const NODE = BUNDLE ? path.join(BUNDLE, 'node', 'bin', 'node') : '';
const LAUNCHER_MAIN = BUNDLE ? path.join(BUNDLE, 'launcher', 'src', 'main.ts') : '';
const ELECTRON_DIR = path.resolve(__dirname, '..', '..', 'electron');
const START_MS = 240_000;

test.describe.configure({ mode: 'serial' });
test.skip(!BUNDLE, 'set PIPESHUB_E2E_BUNDLE to a native bundle folder');

let userData: string;
const dataDir = () => path.join(userData, 'local-server');

function status(): { running?: boolean; url?: string; pid?: number; services?: Record<string, { pid: number }> } | null {
  const r = spawnSync(NODE, [LAUNCHER_MAIN, 'status', '--json', '--data-dir', dataDir()], { encoding: 'utf8' });
  if (r.status !== 0) return null;
  return JSON.parse(r.stdout.trim().split('\n').pop() as string);
}

/** Processes whose command line mentions this test's data folder (launcher, PostgreSQL, Python, Node). */
function leftovers(): string[] {
  const out = execFileSync('ps', ['-eo', 'pid=,args='], { encoding: 'utf8' });
  return out.split('\n').filter((l) => l.includes(userData) && !l.includes(`${process.pid} `) && !/\bps -eo\b/.test(l));
}

async function launch(): Promise<ElectronApplication> {
  const env = { ...process.env, HOME: path.join(userData, 'home'), PIPESHUB_LAUNCHER: NODE, PIPESHUB_LAUNCHER_ARGS: JSON.stringify([LAUNCHER_MAIN]) } as Record<string, string>;
  // PIPESHUB_E2E_PACKAGED=<built app executable>: run the packaged app, which must find its own bundled launcher.
  const packaged = process.env.PIPESHUB_E2E_PACKAGED;
  if (packaged) {
    delete env.PIPESHUB_LAUNCHER;
    return electron.launch({ executablePath: packaged, args: [`--user-data-dir=${userData}`], env });
  }
  return electron.launch({ args: [ELECTRON_DIR, '--no-sandbox', `--user-data-dir=${userData}`], env });
}

async function quit(app: ElectronApplication): Promise<void> {
  const exited = new Promise<void>((resolve) => app.process().once('exit', () => resolve()));
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await app.evaluate((electronModule) => (electronModule as any).app.quit());
  await exited;
}

async function expectSignInRoute(page: Page): Promise<void> {
  await expect.poll(() => page.url(), { timeout: START_MS }).toMatch(/^app:\/\/.*\/(login|sign-up)\/?$/);
}

/** First launch of a fresh data folder asks; a later one (choice remembered) goes straight on. */
async function chooseLocalIfAsked(page: Page): Promise<void> {
  const choice = page.getByTestId('mode-local');
  if (await choice.isVisible({ timeout: 5_000 }).catch(() => false)) await choice.click();
}

test.beforeAll(() => {
  userData = fs.mkdtempSync(path.join(os.tmpdir(), 'ph-real-'));
});
test.afterAll(() => {
  spawnSync(NODE, [LAUNCHER_MAIN, 'stop', '--data-dir', dataDir()]);
  fs.rmSync(userData, { recursive: true, force: true });
});

test('first run -> local mode -> UI at the sign-in route -> quit leaves nothing running', async () => {
  test.setTimeout(420_000);
  const app = await launch();
  const page = await app.firstWindow();
  await page.getByTestId('mode-local').click({ timeout: 60_000 });
  await expectSignInRoute(page);
  const s = status();
  expect(s?.running).toBe(true);
  expect(s?.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
  expect((await fetch(`${s!.url}/api/v1/health`)).status).toBe(200);
  // This launch's URL is held in memory only: the port changes at every start, so it is never saved.
  expect(await page.evaluate(() => window.localStorage.getItem('PIPESHUB_API_BASE_URL'))).toBeNull();

  await quit(app);
  expect(status()).toBeNull();
  await expect.poll(leftovers, { timeout: 30_000 }).toEqual([]);
});

test('a launcher left running by a killed app is adopted, then stopped on quit', async () => {
  test.setTimeout(420_000);
  const first = await launch(); // choice is remembered: starts without asking
  const p1 = await first.firstWindow();
  await expectSignInRoute(p1);
  const before = status();
  expect(before?.running).toBe(true);
  first.process().kill('SIGKILL'); // the app dies without stopping PipesHub
  await new Promise((r) => setTimeout(r, 3000));
  expect(status()?.pid).toBe(before?.pid);

  const second = await launch();
  const p2 = await second.firstWindow();
  await expectSignInRoute(p2);
  const after = status();
  expect(after?.pid).toBe(before?.pid); // same launcher: adopted, not restarted
  expect(after?.url).toBe(before?.url);
  await quit(second);
  expect(status()).toBeNull();
  await expect.poll(leftovers, { timeout: 30_000 }).toEqual([]);
});

test('a service dying shows the error screen, and Retry brings PipesHub back', async () => {
  test.setTimeout(420_000);
  const app = await launch();
  const page = await app.firstWindow();
  await chooseLocalIfAsked(page);
  await expectSignInRoute(page);
  const pid = status()?.services?.node?.pid;
  expect(pid).toBeGreaterThan(0);
  process.kill(pid as number, 'SIGKILL');
  await expect(page.getByTestId('local-retry')).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId('local-error-details')).toBeVisible();
  await page.getByTestId('local-retry').click();
  // A new launcher starts (the old one exited with the dead service); wait until it is ready and the UI is back.
  await expect.poll(() => (status() as { phase?: string } | null)?.phase, { timeout: START_MS }).toBe('ready');
  await expect(page.getByTestId('local-starting')).toBeHidden({ timeout: START_MS });
  await expect(page.getByTestId('local-retry')).toBeHidden();
  await expectSignInRoute(page);
  expect((await fetch(`${status()!.url}/api/v1/health`)).status).toBe(200);
  await quit(app);
  expect(status()).toBeNull();
  await expect.poll(leftovers, { timeout: 30_000 }).toEqual([]);
});
