/**
 * Stand-in for `pipeshub-native`, speaking the contract in
 * local-server/launcher-client.ts. Used by the unit tests and the e2e so
 * neither needs the multi-GB real bundle.
 *
 * STUB_MODE: ok (default) | slow | unhealthy | crash_before_ready |
 *            crash_after_ready | port_in_use | bad_url | hang |
 *            already_running (start reports it and exits 1; the first `status` says "not running" so the
 *            app takes the start path, later ones report the other instance)
 * Every event is appended to <data-dir>/stub.log; the pid is in <data-dir>/stub.pid.
 */
import * as fs from 'fs';
import * as http from 'http';
import * as path from 'path';

const [sub, ...rest] = process.argv.slice(2);
const dataDir = rest[rest.indexOf('--data-dir') + 1];
const modeFile = path.join(dataDir, 'stub-mode');
// <data-dir>/stub-mode overrides STUB_MODE so a test can change behaviour between retries.
const mode = (fs.existsSync(modeFile) ? fs.readFileSync(modeFile, 'utf8').trim() : process.env.STUB_MODE) || 'ok';
const pidFile = path.join(dataDir, 'stub.pid');
const urlFile = path.join(dataDir, 'stub.url');
const logFile = path.join(dataDir, 'stub.log');

const log = (line: string): void => fs.appendFileSync(logFile, `${line}\n`);
const emit = (obj: unknown): void => void process.stdout.write(`${JSON.stringify(obj)}\n`);
const alive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};
const readPid = (): number | null => {
  try {
    return Number(fs.readFileSync(pidFile, 'utf8')) || null;
  } catch {
    return null;
  }
};

async function start(): Promise<void> {
  fs.mkdirSync(dataDir, { recursive: true });
  log('start');
  if (mode === 'already_running') {
    emit({ event: 'error', code: 'already_running', message: 'PipesHub is already running for this data folder', phase: 'starting' });
    process.exit(1);
  }
  if (mode === 'port_in_use') {
    emit({ event: 'error', code: 'port_in_use', message: 'port 5432 is already in use' });
    process.stderr.write('Error: listen EADDRINUSE: address already in use 127.0.0.1:5432\n');
    process.exit(1);
  }
  fs.writeFileSync(pidFile, String(process.pid));
  emit({ event: 'progress', message: 'initialising database' });
  if (mode === 'crash_before_ready') {
    process.stderr.write('postgres: FATAL: could not write lock file\n');
    process.exit(3);
  }
  const server = http.createServer((req, res) => {
    log(`req ${req.method} ${req.url}`);
    res.setHeader('access-control-allow-origin', '*');
    if (req.url === '/api/v1/health') {
      res.statusCode = mode === 'unhealthy' ? 503 : 200;
      res.setHeader('content-type', 'application/json');
      res.end('{"status":"ok"}');
    } else if (req.url === '/api/v1/org/exists') {
      // Enough for the app to reach its sign-in screen.
      res.setHeader('content-type', 'application/json');
      res.end('{"exists":true}');
    } else if (req.url?.startsWith('/api/')) {
      res.statusCode = 404;
      res.setHeader('content-type', 'application/json');
      res.end('{"error":"stub"}');
    } else {
      res.setHeader('content-type', 'text/html');
      res.end('<title>stub pipeshub</title><h1>stub pipeshub</h1>');
    }
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  fs.writeFileSync(urlFile, url);
  const stop = (): void => {
    log('stop');
    server.close();
    fs.rmSync(pidFile, { force: true });
    process.exit(0);
  };
  if (mode !== 'hang') process.on('SIGTERM', stop);
  else process.on('SIGTERM', () => log('ignored SIGTERM'));
  if (mode === 'slow') await new Promise((r) => setTimeout(r, Number(process.env.STUB_DELAY_MS || 1500)));
  emit({ event: 'ready', url: mode === 'bad_url' ? 'http://example.com:80' : url, healthy_after_s: 1 });
  if (mode === 'crash_after_ready') {
    setTimeout(() => {
      emit({ event: 'service_exited', service: 'connectors', code: 1, signal: null });
      process.stderr.write('connectors: exited with code 1\n');
      process.exit(3);
    }, 1500);
  }
}

function main(): void {
  if (sub === 'start') void start();
  else if (sub === 'stop') {
    const pid = readPid();
    if (pid && alive(pid)) process.kill(pid, 'SIGTERM');
    emit({ stopped: Boolean(pid) });
  } else if (sub === 'status' || sub === 'health') {
    if (mode === 'already_running' && sub === 'status' && !fs.existsSync(path.join(dataDir, 'status-seen'))) {
      fs.writeFileSync(path.join(dataDir, 'status-seen'), '1');
      process.exit(3);
    }
    const pid = readPid();
    const running = Boolean(pid && alive(pid));
    const url = running ? fs.readFileSync(urlFile, 'utf8') : undefined;
    emit({ running, healthy: running, url });
    // Like the real launcher: status exits 3 when not running, health exits 1 when not healthy.
    process.exit(running ? 0 : sub === 'health' ? 1 : 3);
  } else process.exit(2);
}

main();
