import { spawn as nodeSpawn, type ChildProcess, type SpawnOptions } from 'child_process';
import { EventEmitter } from 'events';

/**
 * Client for the native launcher (`pipeshub-native`), the executable that runs
 * a whole PipesHub on this computer. This header is the contract the app codes
 * against; the launcher's own documentation is the source of truth.
 *
 *   pipeshub-native start  --json --data-dir <dir>   foreground, until TERM/INT or a service dies.
 *       JSON lines on stdout, each with an `event`:
 *         progress {stage, message}                 shown while starting
 *         ready    {url, ports, pid, data_dir}      the ONLY line with `url`: healthy, serving there
 *         service_exited {service, code, signal}    a service died (the launcher then exits 3)
 *         error    {code: already_running|start_failed|not_healthy, message, phase?}
 *         stopping / stopped
 *       Exit: 0 stopped on request, 1 start-up failed, 3 a service died.
 *   pipeshub-native stop   --json --data-dir <dir>   ends that data dir's instance; blocks until gone (usually
 *       5-11 s, up to ~35 s); exit 0 also when none runs.
 *   pipeshub-native status --json --data-dir <dir>   {running, phase: starting|ready|stopping, url?}; exit 3 when not running.
 *   pipeshub-native health --json --data-dir <dir>   exit 0 when healthy.
 *
 * Ports are chosen free at start, so the URL is only ever taken from `ready`.
 * A port conflict cannot happen at launch; a bind race shows up as
 * `service_exited` plus exit 3 and the service's error output, which is why
 * port conflicts are recognised from output text, not from an error code.
 * `already_running` means another instance owns the data dir (possibly still
 * starting): the app waits for it to become healthy and adopts it.
 * Cold start measured 22-96 s on a loaded host; stop up to ~35 s.
 */

export type LocalServerState = 'idle' | 'starting' | 'running' | 'stopping' | 'failed';

export type LocalServerErrorCode =
  | 'launcher_missing'
  | 'spawn_failed'
  | 'port_in_use'
  | 'crashed'
  | 'timeout'
  | 'bad_url';

export interface LocalServerError {
  code: LocalServerErrorCode;
  message: string;
  /** Last lines the launcher printed, for the "show details" part of the error screen. */
  logTail: string[];
}

export interface LocalServerStatus {
  state: LocalServerState;
  /** Human-readable step while starting (launcher-reported, or ours). */
  phase?: string;
  /** Loopback origin of the running server, without a trailing slash. */
  url?: string;
  error?: LocalServerError;
}

export type SpawnFn = (command: string, args: string[], options: SpawnOptions) => ChildProcess;

export interface LauncherClientOptions {
  command: string;
  /** Arguments placed before the subcommand (a script path when `command` is node). */
  args?: string[];
  dataDir: string;
  spawn?: SpawnFn;
  fetchFn?: typeof fetch;
  /** Whole start, from spawn to healthy: 22-96 s measured cold on a loaded host. */
  startTimeoutMs?: number;
  stopTimeoutMs?: number;
  healthIntervalMs?: number;
  extraEnv?: NodeJS.ProcessEnv;
}

const LOG_TAIL_LINES = 50;
const PORT_IN_USE = /EADDRINUSE|address already in use|port\s+\d+\s+(is\s+)?(already\s+)?in use/i;

/** The URL becomes the API base that bearer tokens are sent to: loopback http only. */
export function isLoopbackHttpUrl(raw: unknown): raw is string {
  if (typeof raw !== 'string') return false;
  try {
    const u = new URL(raw);
    return u.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname);
  } catch {
    return false;
  }
}

export interface ParsedLauncherLine {
  url?: string;
  badUrl?: boolean;
  phase?: string;
  errorCode?: string;
  errorMessage?: string;
  /** `service_exited`: which service died. */
  exitedService?: string;
}

/** Lines that are not JSON objects are log output, not protocol. */
export function parseLauncherLine(line: string): ParsedLauncherLine | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith('{')) return null;
  let obj: Record<string, unknown>;
  try {
    obj = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!obj || typeof obj !== 'object') return null;
  const out: ParsedLauncherLine = {};
  if (obj.url !== undefined) {
    if (isLoopbackHttpUrl(obj.url)) out.url = (obj.url as string).replace(/\/+$/, '');
    else out.badUrl = true;
  }
  const phase = obj.message ?? obj.phase;
  if (typeof phase === 'string') out.phase = phase;
  if (obj.event === 'service_exited' && typeof obj.service === 'string') out.exitedService = obj.service;
  if (obj.event === 'error' || obj.error) {
    const err = (typeof obj.error === 'object' && obj.error ? obj.error : obj) as Record<string, unknown>;
    out.errorCode = typeof err.code === 'string' ? err.code : 'error';
    out.errorMessage = typeof err.message === 'string' ? err.message : undefined;
  }
  return out;
}

export class LauncherClient extends EventEmitter {
  private readonly opts: Required<Omit<LauncherClientOptions, 'extraEnv'>> & { extraEnv: NodeJS.ProcessEnv };
  private child: ChildProcess | null = null;
  private current: LocalServerStatus = { state: 'idle' };
  private logTail: string[] = [];
  private startPromise: Promise<LocalServerStatus> | null = null;
  private stopRequested = false;

  constructor(options: LauncherClientOptions) {
    super();
    this.opts = {
      args: [],
      spawn: nodeSpawn as SpawnFn,
      fetchFn: fetch,
      startTimeoutMs: 180_000,
      stopTimeoutMs: 45_000,
      healthIntervalMs: 500,
      extraEnv: {},
      ...options,
    };
  }

  get status(): LocalServerStatus {
    return this.current;
  }

  /** Starts (or re-starts after a failure). Never rejects: the outcome is the returned status. */
  start(): Promise<LocalServerStatus> {
    if (this.startPromise) return this.startPromise;
    if (this.current.state === 'running') return Promise.resolve(this.current);
    this.startPromise = this.doStart().finally(() => {
      this.startPromise = null;
    });
    return this.startPromise;
  }

  async stop(): Promise<void> {
    if (this.current.state === 'idle') return;
    const child = this.child;
    this.stopRequested = true;
    this.set({ state: 'stopping' });
    const exited = child ? this.waitExit(child) : Promise.resolve();
    // The launcher's own stop reaches every service even if our child is only a wrapper.
    await this.runSubcommand('stop', this.opts.stopTimeoutMs).catch(() => undefined);
    if (!child) await this.waitNotRunning();
    if (child && child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
    if (!(await settlesWithin(exited, this.opts.stopTimeoutMs)) && child) {
      child.kill('SIGKILL');
      await settlesWithin(exited, 2000);
    }
    this.child = null;
    this.set({ state: 'idle' });
  }

  /** Last-resort synchronous kill for process exit paths; does not wait. */
  killNow(): void {
    this.child?.kill('SIGKILL');
  }

  private set(next: LocalServerStatus): void {
    this.current = next;
    this.emit('status', next);
  }

  private fail(code: LocalServerErrorCode, message: string): LocalServerStatus {
    const status: LocalServerStatus = {
      state: 'failed',
      error: { code, message, logTail: [...this.logTail] },
    };
    this.set(status);
    return status;
  }

  private async doStart(): Promise<LocalServerStatus> {
    this.stopRequested = false;
    this.logTail = [];
    this.set({ state: 'starting', phase: 'checking' });

    // A launcher left running by a crashed app: reuse it rather than fight it for the data dir.
    const adopted = await this.adopt();
    if (adopted) return adopted;

    return new Promise<LocalServerStatus>((resolve) => {
      let settled = false;
      let ready = false;
      let reportedError: { code: string; message?: string } | null = null;
      let exitedService: string | null = null;
      let child: ChildProcess;
      let timer: NodeJS.Timeout | undefined;
      const finish = (status: LocalServerStatus): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(status);
      };

      try {
        child = this.opts.spawn(
          this.opts.command,
          [...this.opts.args, 'start', '--json', '--data-dir', this.opts.dataDir],
          {
            stdio: ['ignore', 'pipe', 'pipe'],
            windowsHide: true,
            env: { ...process.env, ...this.opts.extraEnv },
          },
        );
      } catch (error) {
        finish(this.fail('spawn_failed', errMessage(error)));
        return;
      }
      this.child = child;

      timer = setTimeout(() => {
        if (settled) return;
        child.kill('SIGKILL');
        finish(this.fail('timeout', `PipesHub did not become healthy within ${Math.round(this.opts.startTimeoutMs / 1000)} s.`));
      }, this.opts.startTimeoutMs);

      const onLine = (line: string): void => {
        if (!line.trim()) return;
        this.pushLog(line);
        const parsed = parseLauncherLine(line);
        if (!parsed) return;
        if (parsed.exitedService) exitedService = parsed.exitedService;
        if (parsed.errorCode) reportedError = { code: parsed.errorCode, message: parsed.errorMessage };
        if (parsed.phase && !ready && this.current.state === 'starting') {
          this.set({ state: 'starting', phase: parsed.phase });
        }
        if (parsed.badUrl && !ready) {
          child.kill('SIGKILL');
          finish(this.fail('bad_url', 'The launcher reported an address that is not on this computer.'));
        } else if (parsed.url && !ready) {
          ready = true;
          void this.confirmHealthy(parsed.url, () => settled).then((ok) => {
            if (settled) return;
            if (ok) {
              this.set({ state: 'running', url: parsed.url });
              finish(this.current);
            } else {
              child.kill('SIGKILL');
              finish(this.fail('timeout', 'PipesHub started but did not answer its health check.'));
            }
          });
        }
      };
      pipeLines(child.stdout, onLine);
      pipeLines(child.stderr, (l) => l.trim() && this.pushLog(l));

      child.once('error', (error: NodeJS.ErrnoException) => {
        finish(this.fail(error.code === 'ENOENT' ? 'launcher_missing' : 'spawn_failed', errMessage(error)));
      });
      child.once('exit', (code, signal) => {
        if (this.child === child) this.child = null;
        if (this.stopRequested) {
          finish({ state: 'idle' });
          return;
        }
        if (!ready && reportedError?.code === 'already_running') {
          // Another instance owns the data dir, maybe still starting: wait for it and use it.
          void this.adoptWhenHealthy().then((adopted) => {
            finish(adopted ?? this.fail('crashed', 'Another PipesHub on this computer is using the same data folder and did not become healthy.'));
          });
          return;
        }
        const failure = classifyExit(code, signal, reportedError, this.logTail, exitedService);
        // After start succeeded this is a crash of a running server; before, a failed start.
        const status = this.fail(failure.code, failure.message);
        finish(status);
      });
    });
  }

  private async adoptWhenHealthy(): Promise<LocalServerStatus | null> {
    const deadline = Date.now() + this.opts.startTimeoutMs;
    while (Date.now() < deadline && !this.stopRequested) {
      const adopted = await this.adopt();
      if (adopted) return adopted;
      await delay(Math.max(this.opts.healthIntervalMs, 500));
    }
    return null;
  }

  private async adopt(): Promise<LocalServerStatus | null> {
    const out = await this.runSubcommand('status', 5000).catch(() => null);
    if (!out) return null;
    const parsed = lastJson(out);
    if (!parsed || parsed.running !== true || !isLoopbackHttpUrl(parsed.url)) return null;
    const url = (parsed.url as string).replace(/\/+$/, '');
    if (!(await this.healthOnce(url))) return null;
    this.set({ state: 'running', url });
    return this.current;
  }

  private async confirmHealthy(url: string, gaveUp: () => boolean): Promise<boolean> {
    const deadline = Date.now() + this.opts.startTimeoutMs;
    while (Date.now() < deadline && !this.stopRequested && !gaveUp()) {
      if (await this.healthOnce(url)) return true;
      await delay(this.opts.healthIntervalMs);
    }
    return false;
  }

  private async healthOnce(url: string): Promise<boolean> {
    try {
      const res = await this.opts.fetchFn(`${url}/api/v1/health`, { signal: AbortSignal.timeout(3000) });
      return res.ok;
    } catch {
      return false;
    }
  }

  /** Adopted instance: no child to wait on, so ask the launcher until it says it is gone. */
  private async waitNotRunning(): Promise<void> {
    const deadline = Date.now() + this.opts.stopTimeoutMs;
    while (Date.now() < deadline) {
      const out = await this.runSubcommand('status', 5000).catch(() => null);
      const parsed = out && lastJson(out);
      if (!parsed || parsed.running !== true) return;
      await delay(100);
    }
  }

  private waitExit(child: ChildProcess): Promise<void> {
    if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
    return new Promise((resolve) => child.once('exit', () => resolve()));
  }

  private pushLog(line: string): void {
    this.logTail.push(line.slice(0, 500));
    if (this.logTail.length > LOG_TAIL_LINES) this.logTail.shift();
  }

  /** Runs `<launcher> <sub> --json --data-dir <dir>` to completion and returns stdout. */
  private runSubcommand(sub: string, timeoutMs: number): Promise<string> {
    return new Promise((resolve, reject) => {
      let child: ChildProcess;
      try {
        child = this.opts.spawn(
          this.opts.command,
          [...this.opts.args, sub, '--json', '--data-dir', this.opts.dataDir],
          { stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true, env: { ...process.env, ...this.opts.extraEnv } },
        );
      } catch (error) {
        reject(error);
        return;
      }
      let out = '';
      child.stdout?.on('data', (d: Buffer) => (out += d.toString('utf8')));
      const timer = setTimeout(() => {
        child.kill('SIGKILL');
        reject(new Error(`${sub} timed out`));
      }, timeoutMs);
      child.once('error', (e) => {
        clearTimeout(timer);
        reject(e);
      });
      child.once('exit', (code) => {
        clearTimeout(timer);
        if (code === 0) resolve(out);
        else reject(new Error(`${sub} exited ${code}`));
      });
    });
  }
}

function classifyExit(
  code: number | null,
  signal: NodeJS.Signals | null,
  reported: { code: string; message?: string } | null,
  logTail: string[],
  exitedService: string | null = null,
): { code: LocalServerErrorCode; message: string } {
  const tail = logTail.join('\n');
  // The launcher reports port_in_use when the data folder's web port is taken (it never moves once the database exists).
  if (reported?.code === 'port_in_use' || PORT_IN_USE.test(tail)) {
    return { code: 'port_in_use', message: reported?.message ?? 'A network port PipesHub needs is already in use.' };
  }
  return {
    code: 'crashed',
    message:
      reported?.message ??
      (exitedService
        ? `The ${exitedService} service of PipesHub stopped unexpectedly.`
        : `PipesHub stopped unexpectedly (${signal ?? `exit code ${code}`}).`),
  };
}

function pipeLines(stream: NodeJS.ReadableStream | null, onLine: (line: string) => void): void {
  if (!stream) return;
  let buf = '';
  stream.on('data', (chunk: Buffer) => {
    buf += chunk.toString('utf8');
    let i: number;
    while ((i = buf.indexOf('\n')) >= 0) {
      onLine(buf.slice(0, i));
      buf = buf.slice(i + 1);
    }
  });
  stream.on('end', () => buf && onLine(buf));
}

function lastJson(text: string): Record<string, unknown> | null {
  for (const line of text.trim().split('\n').reverse()) {
    try {
      const v = JSON.parse(line);
      if (v && typeof v === 'object') return v;
    } catch {
      /* not JSON */
    }
  }
  return null;
}

/** True when `p` settled before `ms`; the timer is cleared so it never keeps the process alive. */
async function settlesWithin(p: Promise<unknown>, ms: number): Promise<boolean> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<boolean>((r) => {
    timer = setTimeout(() => r(false), ms);
  });
  try {
    return await Promise.race([p.then(() => true), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

const delay = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
const errMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e));
