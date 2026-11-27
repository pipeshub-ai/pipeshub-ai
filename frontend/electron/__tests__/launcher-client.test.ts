import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { LauncherClient, parseLauncherLine, type LocalServerStatus } from '../local-server/launcher-client';
import { resolveLauncher } from '../local-server/launcher-path';
import { DesktopSettingsStore } from '../local-server/desktop-settings';

// Runs from electron/compile/__tests__, next to the compiled stub.
const STUB = path.join(__dirname, 'stub-launcher.js');

function setup(mode = 'ok', extra: Record<string, string> = {}, over: Record<string, unknown> = {}) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ph-launcher-'));
  const client = new LauncherClient({
    command: process.execPath,
    args: [STUB],
    dataDir,
    healthIntervalMs: 50,
    startTimeoutMs: 15_000,
    stopTimeoutMs: 5_000,
    extraEnv: { STUB_MODE: mode, ...extra },
    ...over,
  });
  const states: string[] = [];
  client.on('status', (s: LocalServerStatus) => states.push(s.state));
  const pid = (): number => Number(fs.readFileSync(path.join(dataDir, 'stub.pid'), 'utf8'));
  const log = (): string => fs.readFileSync(path.join(dataDir, 'stub.log'), 'utf8');
  const cleanup = async (): Promise<void> => {
    await client.stop();
    fs.rmSync(dataDir, { recursive: true, force: true });
  };
  return { client, dataDir, states, pid, log, cleanup };
}

const isAlive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

test('start: reports progress, waits for health, returns the loopback URL', async () => {
  const t = setup();
  const phases: string[] = [];
  t.client.on('status', (s: LocalServerStatus) => s.phase && phases.push(s.phase));
  const status = await t.client.start();
  assert.equal(status.state, 'running');
  assert.match(status.url as string, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.ok(phases.includes('initialising database'));
  const res = await fetch(`${status.url}/api/v1/health`);
  assert.equal(res.status, 200);
  await t.cleanup();
});

test('start waits while the launcher is slow and only then reports running', async () => {
  const t = setup('slow', { STUB_DELAY_MS: '600' });
  const started = Date.now();
  const status = await t.client.start();
  assert.equal(status.state, 'running');
  assert.ok(Date.now() - started >= 500);
  await t.cleanup();
});

test('stop: asks the launcher to stop and leaves no process behind', async () => {
  const t = setup();
  await t.client.start();
  const pid = t.pid();
  assert.ok(isAlive(pid));
  await t.client.stop();
  assert.equal(t.client.status.state, 'idle');
  assert.equal(isAlive(pid), false);
  assert.match(t.log(), /^stop$/m);
  await t.cleanup();
});

test('stop escalates to SIGKILL when the launcher ignores SIGTERM', async () => {
  const t = setup('hang', {}, { stopTimeoutMs: 800 });
  await t.client.start();
  const pid = t.pid();
  await t.client.stop();
  assert.equal(isAlive(pid), false);
  assert.match(t.log(), /ignored SIGTERM/);
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('crash before ready: failed with a crash code and the launcher output', async () => {
  const t = setup('crash_before_ready');
  const status = await t.client.start();
  assert.equal(status.state, 'failed');
  assert.equal(status.error?.code, 'crashed');
  assert.match(status.error?.logTail.join('\n') as string, /could not write lock file/);
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('crash after ready: running turns into failed, and retry starts a fresh server', async () => {
  const t = setup('crash_after_ready');
  const first = await t.client.start();
  assert.equal(first.state, 'running');
  await new Promise<void>((resolve) => {
    t.client.on('status', (s: LocalServerStatus) => s.state === 'failed' && resolve());
  });
  assert.equal(t.client.status.error?.code, 'crashed');
  assert.match(t.client.status.error?.logTail.join('\n') as string, /connectors: exited/);

  // Retry: the same client starts again (the stub now behaves).
  (t.client as unknown as { opts: { extraEnv: Record<string, string> } }).opts.extraEnv.STUB_MODE = 'ok';
  const second = await t.client.start();
  assert.equal(second.state, 'running');
  assert.notEqual(second.url, first.url);
  await t.cleanup();
});

test('port conflict is reported as its own code', async () => {
  const t = setup('port_in_use');
  const status = await t.client.start();
  assert.equal(status.state, 'failed');
  assert.equal(status.error?.code, 'port_in_use');
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('port conflict is also recognised from plain log output', async () => {
  const t = setup('ok', {}, {
    spawn: () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { spawn } = require('child_process');
      return spawn(process.execPath, ['-e', "console.error('listen EADDRINUSE: address already in use'); process.exit(1)"]);
    },
  });
  const status = await t.client.start();
  assert.equal(status.error?.code, 'port_in_use');
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('a missing launcher is reported, not thrown', async () => {
  const t = setup('ok', {}, { command: path.join(os.tmpdir(), 'no-such-launcher'), args: [] });
  const status = await t.client.start();
  assert.equal(status.state, 'failed');
  assert.equal(status.error?.code, 'launcher_missing');
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('a URL that is not on this computer is refused', async () => {
  const t = setup('bad_url');
  const status = await t.client.start();
  assert.equal(status.error?.code, 'bad_url');
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('a server that never passes its health check times out and is killed', async () => {
  const t = setup('unhealthy', {}, { startTimeoutMs: 700 });
  const status = await t.client.start();
  assert.equal(status.error?.code, 'timeout');
  await new Promise((r) => setTimeout(r, 300));
  assert.equal(isAlive(t.pid()), false);
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('a launcher left running by an earlier app run is adopted, not restarted', async () => {
  const first = setup();
  const running = await first.client.start();
  // A second app instance with the same data dir (the first one "crashed" without stopping it).
  const second = new LauncherClient({
    command: process.execPath,
    args: [STUB],
    dataDir: first.dataDir,
    healthIntervalMs: 50,
    extraEnv: { STUB_MODE: 'ok' },
  });
  const adopted = await second.start();
  assert.equal(adopted.state, 'running');
  assert.equal(adopted.url, running.url);
  assert.equal((first.log().match(/^start$/gm) ?? []).length, 1);
  const pid = first.pid();
  await second.stop();
  assert.equal(isAlive(pid), false);
  fs.rmSync(first.dataDir, { recursive: true, force: true });
});

test('already_running (another instance still starting): waits for it and adopts it', async () => {
  const other = setup();
  const running = await other.client.start();
  // The second app instance sees "not running" once, tries to start, is told it is running, and adopts.
  const second = new LauncherClient({
    command: process.execPath,
    args: [STUB],
    dataDir: other.dataDir,
    healthIntervalMs: 50,
    extraEnv: { STUB_MODE: 'already_running' },
  });
  const adopted = await second.start();
  assert.equal(adopted.state, 'running');
  assert.equal(adopted.url, running.url);
  assert.equal((other.log().match(/^start$/gm) ?? []).length, 2); // its own start was refused, not run twice
  await other.cleanup();
});

test('a service death is named in the message (launcher exit 3)', async () => {
  const t = setup('crash_after_ready');
  await t.client.start();
  await new Promise<void>((resolve) => t.client.on('status', (s: LocalServerStatus) => s.state === 'failed' && resolve()));
  assert.match(t.client.status.error?.message as string, /connectors/);
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('concurrent start calls share one launch', async () => {
  const t = setup();
  const [a, b] = await Promise.all([t.client.start(), t.client.start()]);
  assert.equal(a.url, b.url);
  assert.equal((t.log().match(/^start$/gm) ?? []).length, 1);
  await t.cleanup();
});

test('stop while starting ends in idle and leaves nothing running', async () => {
  const t = setup('slow', { STUB_DELAY_MS: '5000' });
  const starting = t.client.start();
  await new Promise((r) => setTimeout(r, 400));
  await t.client.stop();
  const status = await starting;
  assert.equal(status.state, 'idle');
  assert.equal(t.client.status.state, 'idle');
  fs.rmSync(t.dataDir, { recursive: true, force: true });
});

test('parseLauncherLine: protocol lines vs log noise', () => {
  assert.equal(parseLauncherLine('starting postgres...'), null);
  assert.equal(parseLauncherLine('{not json'), null);
  assert.deepEqual(parseLauncherLine('{"url":"http://127.0.0.1:64201/"}'), { url: 'http://127.0.0.1:64201' });
  assert.equal(parseLauncherLine('{"url":"http://evil.example"}')?.badUrl, true);
  assert.equal(parseLauncherLine('{"url":"https://127.0.0.1:1"}')?.badUrl, true);
  assert.equal(parseLauncherLine('{"event":"error","code":"port_in_use","message":"x"}')?.errorCode, 'port_in_use');
  assert.equal(parseLauncherLine('{"event":"progress","message":"loading models"}')?.phase, 'loading models');
});

test('resolveLauncher: the bundle\'s Node runs launcher/src/main.ts; dev override ignored when packaged; missing bundle; no Windows', () => {
  const base = { resourcesPath: '/Applications/PipesHub.app/Contents/Resources', platform: 'darwin' as const, devBundleDir: '/dev/res' };
  const env = { PIPESHUB_LAUNCHER: '/bin/evil', PIPESHUB_LAUNCHER_ARGS: '[]' };
  const bundle = '/Applications/PipesHub.app/Contents/Resources/pipeshub-native';
  const packaged = resolveLauncher({ ...base, isPackaged: true, env, exists: () => true });
  assert.deepEqual(packaged, { command: `${bundle}/node/bin/node`, args: [`${bundle}/launcher/src/main.ts`], bundleDir: bundle });
  assert.equal(resolveLauncher({ ...base, isPackaged: true, env: {}, exists: () => false }), null);
  // a bundle with Node but without the launcher (or the reverse) is not a bundle
  assert.equal(resolveLauncher({ ...base, isPackaged: true, env: {}, exists: (p) => p.endsWith('/node') }), null);
  assert.equal(resolveLauncher({ ...base, isPackaged: true, env: {}, exists: (p) => p.endsWith('main.ts') }), null);
  const dev = resolveLauncher({ ...base, isPackaged: false, env: { PIPESHUB_LAUNCHER: 'node', PIPESHUB_LAUNCHER_ARGS: '["stub.js"]' } });
  assert.deepEqual([dev?.command, dev?.args], ['node', ['stub.js']]);
  assert.equal(resolveLauncher({ ...base, platform: 'win32', isPackaged: true, env: {}, exists: () => true }), null);
});

test('settings: first run is null, choices persist, a corrupt file reads as defaults', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ph-settings-'));
  const file = path.join(dir, 'sub', 'desktop-settings.json');
  const store = new DesktopSettingsStore(file);
  assert.deepEqual(store.get(), { mode: null, keepRunningInTray: false });
  store.update({ mode: 'local', keepRunningInTray: true });
  assert.deepEqual(new DesktopSettingsStore(file).get(), { mode: 'local', keepRunningInTray: true });
  store.update({ mode: 'bogus' as never });
  assert.equal(store.get().mode, 'local');
  fs.writeFileSync(file, '{broken');
  assert.deepEqual(new DesktopSettingsStore(file).get(), { mode: null, keepRunningInTray: false });
  fs.rmSync(dir, { recursive: true, force: true });
});
