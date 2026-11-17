// start / stop / status / health. The launcher owns every other process: PostgreSQL, the Python services process_monitor.sh
// starts (each its own process, with its own database login), the placement and fold workers, and the Node API. Each runs in its
// own process group, so a stop reaches its worker pools.
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import {
  type Ctx, HOST, type Layout, PY_SERVICES, type Ports, type ProcessName, type Secrets, apiUrl, defaultDataDir, layout,
  loadSecrets, pickPorts, postgresConf, readJson, redact, schemaEnv, serviceEnvs, writeJson,
} from "./config.ts";

export type Emit = (event: Record<string, unknown>) => void;

const MESSAGES: Record<string, string> = {
  initdb: "Creating the database", postgres: "Starting the database", schema: "Preparing the database",
  services: "Starting PipesHub", health: "Waiting for PipesHub to answer",
};
/** A progress event: `stage` is stable (for code), `message` is for people. */
export const progress = (emit: Emit, stage: string) => emit({ event: "progress", stage, message: MESSAGES[stage] ?? stage });

export interface Options {
  dataDir?: string;
  bundle?: string;
  portBase?: number;
  stopWaitS?: number;
  healthTimeoutS?: number;
}

export interface State {
  pid: number;
  phase: "starting" | "ready" | "stopping";
  startedAt: number;
  bundle: string;
  dataDir: string;
  url: string;
  ports: Ports;
  services: Partial<Record<ProcessName, number>>;
  postgresPid?: number;
  /** The one-shot step running now (its own process group), so a stop after a killed launcher can end it. */
  step?: { name: string; pid: number };
  healthyAfterS?: number;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
// An exited child nobody has reaped yet still answers kill(0): it counts as gone.
function zombie(pid: number): boolean {
  try {
    if (process.platform === "linux") return readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1]?.startsWith("Z") ?? false;
    return spawnSync("ps", ["-o", "stat=", "-p", String(pid)], { encoding: "utf8" }).stdout.trim().startsWith("Z");
  } catch { return false; }
}
export const alive = (pid: number | undefined): boolean => {
  if (!pid) return false;
  try { process.kill(pid, 0); } catch (e) { return (e as NodeJS.ErrnoException).code === "EPERM"; }
  return !zombie(pid);
};
const group = (pid: number, signal: NodeJS.Signals) => { try { process.kill(-pid, signal); } catch { /* already gone */ } };

/** The bundle is the folder above the launcher's own (<bundle>/launcher/src/main.ts) unless told otherwise. */
export function resolve(o: Options, env = process.env, self = import.meta.dirname): { bundle: string; dataDir: string; lay: Layout } {
  // Absolute: steps run with the data folder as their working directory, where a relative path would point elsewhere.
  const bundle = resolvePath(o.bundle ?? env.PIPESHUB_BUNDLE ?? join(self, "..", ".."));
  const dataDir = resolvePath(o.dataDir ?? env.PIPESHUB_HOME ?? defaultDataDir(env));
  return { bundle, dataDir, lay: layout(bundle, dataDir) };
}

export const statePath = (dataDir: string) => join(dataDir, "launcher.json");
export const readState = (dataDir: string) => readJson<State>(statePath(dataDir));

/** The state of a launcher that is running, or undefined (a state file left by a crash is not "running"). */
export function running(dataDir: string): State | undefined {
  const s = readState(dataDir);
  return s && alive(s.pid) ? s : undefined;
}

export const lockPath = (dataDir: string) => join(dataDir, "launcher.lock");

const readLockPid = (path: string): number | null => {
  try { const t = readFileSync(path, "utf8").trim(); return t ? Number(t) || null : 0; } catch { return null; }
};
const ageMs = (path: string): number => { try { return Date.now() - statSync(path).mtimeMs; } catch { return Infinity; } };

/** The lock's pid when it is held: a live pid, or a lock still being written (empty for at most a few seconds). */
function lockHolder(dataDir: string): number | undefined {
  const pid = readLockPid(lockPath(dataDir));
  if (pid === null) return undefined;
  if (pid === 0) return ageMs(lockPath(dataDir)) < 5000 ? -1 : undefined;
  return alive(pid) ? pid : undefined;
}

/**
 * Removes a lock whose holder is dead. Only the process that creates `launcher.lock.break` (exclusively) may do it, and it
 * looks again under that guard, so two starts that both saw the same dead pid cannot delete each other's new lock. A guard
 * left by a process that died mid-takeover is cleared after 30 s. Returns false when another process is taking over now.
 */
function breakStaleLock(dataDir: string, hooks: LockHooks = {}): boolean {
  const guard = `${lockPath(dataDir)}.break`;
  try {
    writeFileSync(guard, String(process.pid), { flag: "wx", mode: 0o600 });
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e;
    if (ageMs(guard) > 30_000) rmSync(guard, { force: true });
    return false;
  }
  try {
    hooks.beforeBreak?.();
    if (lockHolder(dataDir) === undefined) rmSync(lockPath(dataDir), { force: true });
    return true;
  } finally {
    rmSync(guard, { force: true });
  }
}

export interface LockHooks { beforeBreak?: () => void }

/**
 * One launcher per data folder: the lock file is created exclusively before anything else happens, so two starts at once
 * cannot both run initdb. A lock whose holder died is removed under a guard (breakStaleLock), then created again exclusively.
 * Returns undefined when this process holds the lock, else the holder's pid (-1 when another start is taking it over).
 */
export function takeLock(dataDir: string, hooks: LockHooks = {}): number | undefined {
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      writeFileSync(lockPath(dataDir), String(process.pid), { flag: "wx", mode: 0o600 });
      return undefined;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e;
    }
    const holder = lockHolder(dataDir);
    if (holder !== undefined) return holder;
    if (!breakStaleLock(dataDir, hooks)) return -1;
  }
  return -1;
}

export function releaseLock(dataDir: string): void {
  if (readLockPid(lockPath(dataDir)) === process.pid) rmSync(lockPath(dataDir), { force: true });
}

function tail(path: string, bytes = 4000): string {
  try {
    const size = statSync(path).size, fd = openSync(path, "r"), buf = Buffer.alloc(Math.min(size, bytes));
    readSync(fd, buf, 0, buf.length, size - buf.length); closeSync(fd);
    return buf.toString("utf8");
  } catch { return ""; }
}

/** The one-shot step running now (initdb, pg_ctl, a schema step), so a stop can end it and its children. */
export interface Steps { current?: ChildProcess; cancelled: boolean; onSpawn?: (name: string, pid: number | undefined) => void }

/** A start that a stop interrupted: not an error to report. */
export class Cancelled extends Error {}

/** Run a command to completion in its own process group, output to logs/<name>.log; on failure throw with the tail of the log, secrets masked. */
function runStep(c: Ctx, steps: Steps, name: string, cmd: string, args: string[], env: Record<string, string>, cwd: string): Promise<void> {
  if (steps.cancelled) return Promise.reject(new Cancelled());
  const log = join(c.layout.logs, `${name}.log`), fd = openSync(log, "a", 0o600);
  return new Promise((res, rej) => {
    const p = spawn(cmd, args, { cwd, env, detached: true, stdio: ["ignore", fd, fd] });
    steps.current = p;
    steps.onSpawn?.(name, p.pid);
    const done = (err?: Error) => {
      closeSync(fd);
      if (steps.current === p) { steps.current = undefined; steps.onSpawn?.(name, undefined); }
      err ? rej(err) : res();
    };
    p.once("error", (e) => done(e));
    p.once("exit", (code, sig) => {
      if (steps.cancelled) return done(new Cancelled());
      done(code === 0 ? undefined : new Error(redact(`${name} failed (${code ?? sig}); see ${log}\n${tail(log)}`, c.secrets)));
    });
  });
}

const CREATE_DB = `import psycopg
c = psycopg.connect(dbname='postgres', autocommit=True)
c.execute("SELECT 1 FROM pg_database WHERE datname = 'pipeshub'").fetchone() or c.execute('CREATE DATABASE pipeshub')`;

const pgEnv = () => ({ PATH: process.env.PATH ?? "/usr/bin:/bin" });

/** First run: initdb. Every run: write the settings, then start PostgreSQL (a leftover server of a crashed launcher is stopped first). */
export async function startPostgres(c: Ctx, emit: Emit, steps: Steps = { cancelled: false }): Promise<void> {
  const { layout: lay } = c, pgCtl = join(lay.pgBin, "pg_ctl");
  if (!existsSync(join(lay.pgData, "PG_VERSION"))) {
    progress(emit, "initdb");
    // Initialised in a side folder and renamed into place only when complete: a crash part-way leaves nothing that looks
    // initialised, and the next start begins again.
    const fresh = `${lay.pgData}.init`;
    rmSync(fresh, { recursive: true, force: true });
    const pwfile = join(lay.dataDir, ".pwfile.tmp");
    writeFileSync(pwfile, c.secrets.pg_admin + "\n", { mode: 0o600 });
    try {
      await runStep(c, steps, "initdb", join(lay.pgBin, "initdb"), ["-D", fresh, "-U", "postgres", "--auth=scram-sha-256", `--pwfile=${pwfile}`,
        "-E", "UTF8", "--locale=C", "--locale-provider=builtin", "--builtin-locale=C.UTF-8", "--no-instructions"], pgEnv(), lay.dataDir);
    } finally { rmSync(pwfile, { force: true }); }
    appendFileSync(join(fresh, "postgresql.conf"), "\ninclude 'launcher.conf'\n");
    rmSync(lay.pgData, { recursive: true, force: true });
    renameSync(fresh, lay.pgData);
  }
  // A data folder whose include went missing (edited, or made by hand) would ignore every setting below.
  const conf = join(lay.pgData, "postgresql.conf");
  if (!/^include 'launcher\.conf'$/m.test(readFileSync(conf, "utf8"))) appendFileSync(conf, "\ninclude 'launcher.conf'\n");
  writeFileSync(join(lay.pgData, "launcher.conf"), postgresConf(c), { mode: 0o600 });
  mkdirSync(lay.run, { recursive: true, mode: 0o700 });
  if (existsSync(join(lay.pgData, "postmaster.pid"))) spawnSync(pgCtl, ["-D", lay.pgData, "-m", "fast", "-w", "-t", "60", "stop"], { env: pgEnv(), stdio: "ignore" });
  progress(emit, "postgres");
  await runStep(c, steps, "pg_ctl", pgCtl, ["-D", lay.pgData, "-l", join(lay.logs, "postgres.log"), "-w", "-t", "60", "start"], pgEnv(), lay.dataDir);
}

/** The server's pid from postmaster.pid, when that file names this data folder and the process is alive. */
export function postmasterPid(lay: Layout): number | undefined {
  try {
    const [pid, dir] = readFileSync(join(lay.pgData, "postmaster.pid"), "utf8").split("\n");
    const n = Number(pid);
    return n && resolvePath(dir ?? "") === resolvePath(lay.pgData) && alive(n) ? n : undefined;
  } catch { return undefined; }
}

/** Stops this data folder's server: pg_ctl first, then a fast-shutdown signal to its postmaster if pg_ctl could not (bundle gone, say). */
export async function stopPostgres(lay: Layout): Promise<void> {
  if (!existsSync(join(lay.pgData, "postmaster.pid"))) return;
  spawnSync(join(lay.pgBin, "pg_ctl"), ["-D", lay.pgData, "-m", "fast", "-w", "-t", "60", "stop"], { env: pgEnv(), stdio: "ignore" });
  const pid = postmasterPid(lay);
  if (!pid) return;
  try { process.kill(pid, "SIGINT"); } catch { return; }
  const until = Date.now() + 60_000;
  while (alive(pid) && Date.now() < until) await sleep(200);
}

/** Database, roles and schema to head, on every start, so an upgrade migrates. Only these steps see the administrator password. */
export async function prepareSchema(c: Ctx, emit: Emit, steps: Steps = { cancelled: false }): Promise<void> {
  const { layout: lay } = c, env = schemaEnv(c);
  progress(emit, "schema");
  await runStep(c, steps, "createdb", lay.python, ["-I", "-c", CREATE_DB], env.createdb, lay.appDir);
  await runStep(c, steps, "bootstrap", lay.python, ["-m", "db.bootstrap_roles"], env.bootstrap, lay.appDir);
  await runStep(c, steps, "migrate", lay.python, ["-m", "db.migrate"], env.migrate, lay.appDir);
}

export async function waitHealthy(url: string, timeoutS: number, stillUp: () => boolean, fetchFn: typeof fetch = fetch): Promise<boolean> {
  const deadline = Date.now() + timeoutS * 1000;
  while (Date.now() < deadline && stillUp()) {
    try { if ((await fetchFn(`${url}/api/v1/health`, { signal: AbortSignal.timeout(2000) })).status === 200) return true; } catch { /* not up yet */ }
    await sleep(500);
  }
  return false;
}

/** Exit codes of `start`: 0 stopped on request, 1 start-up failed, 3 a service died. */
export async function start(o: Options, emit: Emit): Promise<number> {
  const { bundle, dataDir, lay } = resolve(o);
  const t0 = Date.now();
  for (const d of [dataDir, lay.logs, lay.home]) mkdirSync(d, { recursive: true, mode: 0o700 });
  const holder = takeLock(dataDir);
  if (holder !== undefined) {
    emit({ event: "error", code: "already_running", message: holder > 0 ? `already running (pid ${holder})` : "another start is in progress", phase: readState(dataDir)?.phase ?? "starting" });
    return 1;
  }
  await reapLeftovers(dataDir, lay);
  const ports = await pickPorts(readJson<Ports>(join(dataDir, "ports.json")), o.portBase);
  writeJson(join(dataDir, "ports.json"), ports);
  const secrets: Secrets = loadSecrets(dataDir);
  const c: Ctx = { layout: lay, ports, secrets };
  const state: State = { pid: process.pid, phase: "starting", startedAt: t0, bundle, dataDir, url: apiUrl(c), ports, services: {} };
  const save = () => writeJson(statePath(dataDir), state);
  save();

  const procs = new Map<ProcessName, ChildProcess>();
  // The step's pid is written down before it is awaited: if this launcher is killed, stop can still end that step.
  const steps: Steps = {
    cancelled: false,
    onSpawn: (name, pid) => { state.step = pid ? { name, pid } : undefined; save(); },
  };
  let stopping = false, exitCode = 0;
  let wake!: () => void;
  const stopRequested = new Promise<void>((r) => { wake = r; });
  // Asking to stop ends the step in progress (its whole process group); the start sequence then unwinds and the shutdown
  // below runs once, after it, so a server that step started is stopped too.
  const requestStop = (code: number) => {
    if (stopping) return;
    stopping = true; exitCode = code; steps.cancelled = true;
    if (steps.current?.pid) group(steps.current.pid, "SIGTERM");
    wake();
  };
  const onSignal = () => requestStop(0);
  process.once("SIGTERM", onSignal);
  process.once("SIGINT", onSignal);

  const spawnService = (name: ProcessName, cmd: string, args: string[], cwd: string, env: Record<string, string>) => {
    const fd = openSync(join(lay.logs, `${name}.log`), "a", 0o600);
    const p = spawn(cmd, args, { cwd, env, detached: true, stdio: ["ignore", fd, fd] });
    closeSync(fd);
    procs.set(name, p);
    if (p.pid) { state.services[name] = p.pid; save(); }
    // A binary that is missing or cannot run fails here, often with no exit event after it.
    p.once("error", (e) => {
      if (stopping) return;
      emit({ event: "error", code: "start_failed", message: redact(`${name} could not start: ${e.message}`, secrets) });
      requestStop(1);
    });
    p.once("exit", (code, sig) => {
      if (stopping) return;
      emit({ event: "service_exited", service: name, code, signal: sig });
      requestStop(3);
    });
  };

  try {
    await startPostgres(c, emit, steps);
    state.postgresPid = postmasterPid(lay); save();
    await prepareSchema(c, emit, steps);
    if (stopping) throw new Cancelled();
    progress(emit, "services");
    const envs = serviceEnvs(c);
    for (const svc of PY_SERVICES) spawnService(svc, lay.python, ["-m", `app.${svc}_main`], lay.pythonDir, envs[svc]);
    spawnService("placement", lay.python, ["-m", "db.maintain", "placement", "--watch"], lay.appDir, envs.placement);
    spawnService("fold", lay.python, ["-m", "db.maintain", "fold", "--watch"], lay.appDir, envs.fold);
    spawnService("node", lay.node, ["dist/index.js"], lay.backendDir, envs.node);
    progress(emit, "health");
    const ok = await waitHealthy(`http://${HOST}:${ports.api}`, o.healthTimeoutS ?? 300, () => !stopping);
    if (!ok && !stopping) { emit({ event: "error", code: "not_healthy", message: `not healthy; see ${lay.logs}` }); requestStop(1); }
    if (!stopping) {
      state.phase = "ready"; state.healthyAfterS = +((Date.now() - t0) / 1000).toFixed(1); save();
      emit({ event: "ready", url: state.url, ports, healthy_after_s: state.healthyAfterS, pid: process.pid, data_dir: dataDir });
    }
  } catch (e) {
    if (!(e instanceof Cancelled) && !stopping) emit({ event: "error", code: "start_failed", message: redact((e as Error).message, secrets) });
    requestStop(stopping ? exitCode : 1);
  }
  await stopRequested;

  state.phase = "stopping"; save();
  emit({ event: "stopping" });
  const pids = [...procs.values()].map((p) => p.pid).filter((p): p is number => !!p);
  pids.forEach((p) => group(p, "SIGTERM"));
  const until = Date.now() + (o.stopWaitS ?? 30) * 1000;
  while (Date.now() < until && pids.some((p) => alive(p))) await sleep(200);
  pids.forEach((p) => group(p, "SIGKILL"));
  await stopPostgres(lay);
  rmSync(statePath(dataDir), { force: true });
  releaseLock(dataDir);
  process.off("SIGTERM", onSignal); process.off("SIGINT", onSignal);
  emit({ event: "stopped" });
  return exitCode;
}

/** Processes a crashed launcher left behind: their groups are ended, so the new run owns every port and the database. */
export async function reapLeftovers(dataDir: string, lay: Layout): Promise<void> {
  const s = readState(dataDir);
  if (s && alive(s.pid)) return;
  // A step (initdb, pg_ctl, a schema step) runs in its own group and outlives a killed launcher: end it before anything
  // else touches the data folder, or a new initdb or a late pg_ctl start would run alongside it.
  if (s?.step?.pid && alive(s.step.pid)) {
    group(s.step.pid, "SIGTERM");
    const until = Date.now() + 10_000;
    while (alive(s.step.pid) && Date.now() < until) await sleep(100);
    group(s.step.pid, "SIGKILL");
  }
  for (const pid of Object.values(s?.services ?? {})) if (pid && alive(pid)) group(pid, "SIGKILL");
  // The server is not one of the services: pg_ctl started it, so it outlives a killed launcher.
  await stopPostgres(lay);
  rmSync(statePath(dataDir), { force: true });
}

export async function stop(o: Options, emit: Emit): Promise<number> {
  const { dataDir, lay } = resolve(o);
  const s = running(dataDir);
  if (!s) {
    // A start can be past its lock but not yet past writing its state: then the lock names it.
    const holder = lockHolder(dataDir);
    if (holder && holder > 0) return stopPid(holder, dataDir, lay, o, emit);
    await reapLeftovers(dataDir, lay);
    if (lockHolder(dataDir) === undefined && existsSync(lockPath(dataDir))) breakStaleLock(dataDir);
    emit({ event: "stopped", was_running: false }); return 0;
  }
  return stopPid(s.pid, dataDir, lay, o, emit);
}

async function stopPid(pid: number, dataDir: string, lay: Layout, o: Options, emit: Emit): Promise<number> {
  try { process.kill(pid, "SIGTERM"); } catch { /* already gone */ }
  const until = Date.now() + ((o.stopWaitS ?? 30) + 40) * 1000;
  while (alive(pid) && Date.now() < until) await sleep(200);
  if (alive(pid)) { emit({ event: "error", code: "stop_failed", message: `launcher ${pid} did not stop` }); return 1; }
  // If the launcher died rather than stopped, its step or server may still be up.
  await reapLeftovers(dataDir, lay);
  emit({ event: "stopped", was_running: true });
  return 0;
}

export function status(o: Options): { code: number; body: Record<string, unknown> } {
  const { dataDir } = resolve(o);
  const s = running(dataDir);
  if (!s) return { code: 3, body: { running: false, data_dir: dataDir } };
  const services = Object.fromEntries(Object.entries(s.services).map(([n, pid]) => [n, { pid, alive: alive(pid) }]));
  return { code: 0, body: { running: true, phase: s.phase, pid: s.pid, url: s.url, ports: s.ports, services, data_dir: dataDir, healthy_after_s: s.healthyAfterS ?? null } };
}

export async function health(o: Options, fetchFn: typeof fetch = fetch): Promise<{ code: number; body: Record<string, unknown> }> {
  const st = status(o);
  if (!st.body.running) return { code: 1, body: { healthy: false, reason: "not running" } };
  const ports = st.body.ports as Ports;
  const services = st.body.services as Record<string, { alive: boolean }>;
  const checks: Record<string, boolean> = Object.fromEntries(Object.entries(services).map(([n, x]) => [n, x.alive]));
  checks.api = false; checks.pg = false;
  try { checks.api = (await fetchFn(`http://${HOST}:${ports.api}/api/v1/health`, { signal: AbortSignal.timeout(3000) })).status === 200; } catch { /* down */ }
  try { checks.pg = alive(Number(readFileSync(join(resolve(o).lay.pgData, "postmaster.pid"), "utf8").split("\n")[0])); } catch { /* no server */ }
  const healthy = st.body.phase === "ready" && Object.values(checks).every(Boolean);
  return { code: healthy ? 0 : 1, body: { healthy, phase: st.body.phase, url: st.body.url, checks } };
}

/** `start --detach`: run the launcher in the background and return once it is ready (or failed, with its own error). */
export async function startDetached(o: Options, emit: Emit, args: string[], selfCmd: string[]): Promise<number> {
  const { dataDir, lay } = resolve(o);
  mkdirSync(lay.logs, { recursive: true, mode: 0o700 });
  const holder = lockHolder(dataDir);
  if (holder !== undefined) {
    emit({ event: "error", code: "already_running", message: holder > 0 ? `already running (pid ${holder})` : "another start is in progress", phase: readState(dataDir)?.phase ?? "starting" });
    return 1;
  }
  // Appended, not truncated: a second start racing this one must not wipe its log; this run's lines start at `from`.
  const log = join(lay.logs, "launcher.log"), fd = openSync(log, "a", 0o600);
  const from = statSync(log).size;
  const p = spawn(selfCmd[0]!, [...selfCmd.slice(1), "start", "--json", ...args], { detached: true, stdio: ["ignore", fd, fd] });
  p.unref(); closeSync(fd);
  const until = Date.now() + ((o.healthTimeoutS ?? 300) + 120) * 1000;
  let exited = false; p.once("exit", () => { exited = true; });
  while (Date.now() < until) {
    const s = readState(dataDir);
    if (s && s.pid === p.pid && s.phase === "ready") { emit({ event: "ready", url: s.url, ports: s.ports, healthy_after_s: s.healthyAfterS, pid: s.pid, data_dir: dataDir }); return 0; }
    if (exited) break;
    await sleep(300);
  }
  const mine = (() => { try { return readFileSync(log, "utf8").slice(from); } catch { return ""; } })();
  // The background launcher's own error (already_running, start_failed, ...) says more than "not ready".
  const own = mine.split("\n").reverse().map((l) => { try { return JSON.parse(l) as Record<string, unknown>; } catch { return null; } })
    .find((e) => e?.event === "error");
  emit(own ?? { event: "error", code: "not_ready", message: `did not become ready; see ${log}`, log: mine.slice(-2000) });
  return 1;
}
