// start / stop / status / health. The launcher owns every other process: PostgreSQL, the Python services process_monitor.sh
// starts (each its own process, with its own database login), the placement and fold workers, and the Node API. Each runs in its
// own process group, so a stop reaches its worker pools.
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import {
  appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, readdirSync, readlinkSync, realpathSync, renameSync, rmSync, rmdirSync,
  statSync, writeFileSync,
} from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import {
  type Ctx, HOST, type Layout, PY_SERVICES, type Ports, type ProcessName, type Secrets, apiUrl, defaultDataDir, layout,
  isFree, loadSecrets, pickPorts, postgresConf, readJson, redact, schemaEnv, serviceEnvs, writeJson,
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
  /** The launcher's process identity (boot and start time), so a reused pid is never taken for it. */
  pidId?: string;
  /** The boot the processes below were started in: after a reboot none of them can still be running. */
  bootId?: string;
  serviceIds?: Partial<Record<ProcessName, string>>;
  phase: "starting" | "ready" | "stopping";
  startedAt: number;
  bundle: string;
  dataDir: string;
  url: string;
  ports: Ports;
  services: Partial<Record<ProcessName, number>>;
  postgresPid?: number;
  /** The one-shot step running now (its own process group), so a stop after a killed launcher can end it. */
  step?: { name: string; pid: number; id?: string };
  healthyAfterS?: number;
  /** A crashed launcher's state, kept until its processes are ended, so a crash during that cleanup does not lose them. */
  leftover?: State;
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
// pid 1 or 0 would make -pid every process of this user, or this launcher's own group: never from a state file.
const group = (pid: number, signal: NodeJS.Signals) => { if (pid > 1) try { process.kill(-pid, signal); } catch { /* already gone */ } };

/** This boot's id: Linux's boot_id, macOS's boot time. */
export function bootId(): string | undefined {
  try {
    if (process.platform === "linux") return readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim();
    return spawnSync("sysctl", ["-n", "kern.boottime"], { encoding: "utf8" }).stdout.trim() || undefined;
  } catch { return undefined; }
}

/** A process's identity: its start time (with the boot on Linux), so a pid reused by another process does not match. */
export function procIdentity(pid: number): string | undefined {
  try {
    if (process.platform === "linux") {
      const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
      return `${bootId()}:${stat.slice(stat.lastIndexOf(")") + 2).split(" ")[19]}`;
    }
    return spawnSync("ps", ["-o", "lstart=", "-p", String(pid)], { encoding: "utf8" }).stdout.trim() || undefined;
  } catch { return undefined; }
}

/** The pid is alive and is the same process that was recorded (no identity recorded: the pid alone, as before). */
export const sameProcess = (pid: number | undefined, id: string | undefined): boolean =>
  alive(pid) && (id === undefined || procIdentity(pid!) === id);

/** A process group still has a member. Only ESRCH proves it is gone; any other answer counts as alive. */
export const groupAlive = (pgid: number): boolean => {
  if (pgid <= 1) return false;
  try { process.kill(-pgid, 0); return true; } catch (e) { return (e as NodeJS.ErrnoException).code !== "ESRCH"; }
};

/** SIGKILL is not proof of exit (a process in an uninterruptible wait outlives it): wait until each group is gone, or throw. */
async function killGroups(pgids: number[], waitMs = 10_000): Promise<void> {
  pgids.forEach((p) => group(p, "SIGKILL"));
  const until = Date.now() + waitMs;
  while (pgids.some(groupAlive) && Date.now() < until) await sleep(100);
  const left = pgids.filter(groupAlive);
  if (left.length) throw new Error(`process groups ${left.join(", ")} are still running after SIGKILL`);
}

/**
 * A group this launcher started is still there. While the group exists its leader's pid cannot be handed to another
 * process, so a group whose leader exited but whose workers live is still ours; a live leader must match its recorded identity.
 */
const ownGroupAlive = (pgid: number, id: string | undefined): boolean =>
  groupAlive(pgid) && (!alive(pgid) || id === undefined || procIdentity(pgid) === id);

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
  return s && sameProcess(s.pid, s.pidId) ? s : undefined;
}

export const lockPath = (dataDir: string) => join(dataDir, "launcher.lock");

// The lock is an flock held by a small helper process for the launcher's lifetime: the kernel drops it when the holder dies,
// so there is no stale lock to take over and no race in taking one over. Node has no flock of its own; the bundle's Python does.
const LOCK_HELPER = `import fcntl, os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
except OSError as e:
    print("error", e, flush=True)
    sys.exit(2)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("busy", flush=True)
    sys.exit(3)
print("locked", flush=True)
sys.stdin.read()`;

export interface Lock { release: () => void; lost: Promise<void> }

/** Takes this data folder's lock, or answers "busy" when another launcher holds it. Throws when the lock cannot be taken at all. */
export async function acquireLock(dataDir: string, python: string): Promise<Lock | "busy"> {
  const p = spawn(python, ["-I", "-c", LOCK_HELPER, lockPath(dataDir)], { stdio: ["pipe", "pipe", "pipe"], env: { PATH: process.env.PATH ?? "/usr/bin:/bin" } });
  const exited = new Promise<void>((r) => p.once("exit", () => r()));
  let err = "";
  p.stderr!.on("data", (c) => { err += c; });
  const first = await new Promise<string>((res) => {
    let buf = "";
    p.stdout!.on("data", (c) => { buf += c; const i = buf.indexOf("\n"); if (i >= 0) res(buf.slice(0, i)); });
    p.once("error", (e) => res(`error ${e.message}`));
    // close, not exit: by then the helper's "busy" line has been read
    p.once("close", (code) => res(buf.split("\n")[0] || `error the lock helper exited (${code}) ${err.trim()}`));
  });
  if (first === "locked") return { release: () => { p.stdin!.end(); }, lost: exited };
  if (first === "busy") return "busy";
  throw new Error(`cannot take ${lockPath(dataDir)}: ${first.replace(/^error /, "")}`);
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

/** A start-up failure with its own error code (port_in_use, ...). */
class StartError extends Error {
  readonly code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}

/** Run a command to completion in its own process group, output to logs/<name>.log; on failure throw with the tail of the log, secrets masked. */
function runStep(c: Ctx, steps: Steps, name: string, cmd: string, args: string[], env: Record<string, string>, cwd: string): Promise<void> {
  if (steps.cancelled) return Promise.reject(new Cancelled());
  const log = join(c.layout.logs, `${name}.log`), fd = openSync(log, "a", 0o600);
  return new Promise((res, rej) => {
    const p = spawn(cmd, args, { cwd, env, detached: true, stdio: ["ignore", fd, fd] });
    steps.current = p;
    let recordErr: Error | undefined;
    const done = (err?: Error) => {
      closeSync(fd);
      err ??= recordErr;
      if (steps.current === p) {
        steps.current = undefined;
        // Thrown here it would escape the exit callback and leave the promise (and the shutdown waiting on it) unsettled.
        try { steps.onSpawn?.(name, undefined); } catch (e) { err ??= e as Error; }
      }
      err ? rej(err) : res();
    };
    p.once("error", (e) => done(e));
    p.once("exit", (code, sig) => {
      if (steps.cancelled) return done(new Cancelled());
      done(code === 0 ? undefined : new Error(redact(`${name} failed (${code ?? sig}); see ${log}\n${tail(log)}`, c.secrets)));
    });
    // Recorded once the handlers are in place: if the record fails, the step is still awaited (a pg_ctl start's server must be
    // up and in postmaster.pid before the shutdown looks for it) and its failure comes after.
    try { steps.onSpawn?.(name, p.pid); } catch (e) { recordErr = e as Error; }
  });
}

// Also gives every login that already exists its password from secrets.json: bootstrap_roles sets one only when it creates the
// login, so a key filled in after a restore would otherwise never match. Sent as a SCRAM verifier, never in clear text.
const CREATE_DB = `import os, sys
sys.path.insert(0, os.getcwd())
import psycopg
from psycopg import sql
from db.bootstrap_roles import PASSWORD_VARS
c = psycopg.connect(dbname='postgres', autocommit=True)
c.execute("SELECT 1 FROM pg_database WHERE datname = 'pipeshub'").fetchone() or c.execute('CREATE DATABASE pipeshub')
for role, var in PASSWORD_VARS.items():
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
        v = c.pgconn.encrypt_password(os.environ[var].encode(), role.encode(), b"scram-sha-256").decode()
        c.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(v)))`;

const pgEnv = () => ({ PATH: process.env.PATH ?? "/usr/bin:/bin" });
export const nonEmpty = (dir: string) => existsSync(dir) && readdirSync(dir).length > 0;

/** First run: initdb. Every run: write the settings, then start PostgreSQL (a leftover server of a crashed launcher is stopped first). */
export async function startPostgres(c: Ctx, emit: Emit, steps: Steps = { cancelled: false }): Promise<void> {
  const { layout: lay } = c, pgCtl = join(lay.pgBin, "pg_ctl");
  if (!existsSync(join(lay.pgData, "PG_VERSION"))) {
    // Files without PG_VERSION may be a cluster that lost only that file: never initialise over it.
    if (nonEmpty(lay.pgData)) {
      throw new Error(`${lay.pgData} holds files but no PG_VERSION, so it is not a database this launcher can start, and it will not replace it. Restore it from a backup or move it aside, then start again.`);
    }
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
    if (existsSync(lay.pgData)) rmdirSync(lay.pgData); // empty, as checked above; rmdir refuses anything else
    renameSync(fresh, lay.pgData);
  }
  // A data folder whose include went missing (edited, or made by hand) would ignore every setting below.
  const conf = join(lay.pgData, "postgresql.conf");
  if (!/^include 'launcher\.conf'$/m.test(readFileSync(conf, "utf8"))) appendFileSync(conf, "\ninclude 'launcher.conf'\n");
  writeFileSync(join(lay.pgData, "launcher.conf"), postgresConf(c), { mode: 0o600 });
  mkdirSync(lay.run, { recursive: true, mode: 0o700 });
  await stopPostgres(lay);
  progress(emit, "postgres");
  await runStep(c, steps, "pg_ctl", pgCtl, ["-D", lay.pgData, "-l", join(lay.logs, "postgres.log"), "-w", "-t", "60", "start"], pgEnv(), lay.dataDir);
}

function cwdOf(pid: number): string | undefined {
  if (process.platform === "linux") return readlinkSync(`/proc/${pid}/cwd`);
  return spawnSync("lsof", ["-a", "-p", String(pid), "-d", "cwd", "-Fn"], { encoding: "utf8" }).stdout?.split("\n").find((l) => l.startsWith("n"))?.slice(1);
}

/**
 * The server's pid from postmaster.pid, when that file names this data folder and the process is alive and works in that folder
 * (a postmaster changes into its data folder), so a pid a crashed server left there and another process now has is never taken for it.
 */
export function postmasterPid(lay: Layout): number | undefined {
  try {
    const [pid, dir] = readFileSync(join(lay.pgData, "postmaster.pid"), "utf8").split("\n");
    const n = Number(pid);
    return n && resolvePath(dir ?? "") === resolvePath(lay.pgData) && alive(n) && cwdOf(n) === realpathSync(lay.pgData) ? n : undefined;
  } catch { return undefined; }
}

/**
 * Stops this data folder's server: pg_ctl first, then a fast-shutdown signal to its postmaster if pg_ctl could not (bundle gone, say).
 * Throws when the server is still running at the end, so no caller reports a stop that did not happen.
 */
export async function stopPostgres(lay: Layout, waitMs = 60_000): Promise<void> {
  // pg_ctl signals whatever pid postmaster.pid names, so it runs only for a server proven to be this folder's.
  if (!postmasterPid(lay)) return;
  spawnSync(join(lay.pgBin, "pg_ctl"), ["-D", lay.pgData, "-m", "fast", "-w", "-t", "60", "stop"], { env: pgEnv(), stdio: "ignore" });
  const pid = postmasterPid(lay);
  if (!pid) return;
  try { process.kill(pid, "SIGINT"); } catch { /* exiting */ }
  const until = Date.now() + waitMs;
  while (alive(pid) && Date.now() < until) await sleep(200);
  if (alive(pid)) throw new Error(`PostgreSQL (pid ${pid}) did not stop; its data folder is ${lay.pgData}`);
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
  let lock: Lock | "busy";
  try { lock = await acquireLock(dataDir, lay.python); } catch (e) {
    emit({ event: "error", code: "start_failed", message: (e as Error).message }); return 1;
  }
  if (lock === "busy") {
    const other = running(dataDir);
    emit({ event: "error", code: "already_running", message: other ? `already running (pid ${other.pid})` : "another start is in progress", phase: other?.phase ?? "starting" });
    return 1;
  }
  const held = lock;
  // This start is on record (and answers SIGTERM) before it cleans up what a crashed launcher left, which can take a while:
  // a stop issued meanwhile finds it and stops it instead of timing out.
  const previous = readState(dataDir);
  const state: State = {
    pid: process.pid, pidId: procIdentity(process.pid), bootId: bootId(), phase: "starting", startedAt: t0, bundle, dataDir,
    url: "", ports: {} as Ports, services: {}, serviceIds: {},
    // A previous start that failed before running anything of its own adds no work: keep only what it still held.
    leftover: previous && !previous.step && !Object.keys(previous.services ?? {}).length ? previous.leftover : previous,
  };
  const save = () => writeJson(statePath(dataDir), state);
  // Nothing is started yet: free the lock and let the caller report it. A failed write leaves the old state file whole.
  try { save(); } catch (e) { held.release(); throw e; }
  let secrets: Secrets | undefined;
  const mask = (text: string) => (secrets ? redact(text, secrets) : text);

  const procs = new Map<ProcessName, ChildProcess>();
  // The step's pid is written down before it is awaited: if this launcher is killed, stop can still end that step.
  const steps: Steps = {
    cancelled: false,
    onSpawn: (name, pid) => { state.step = pid ? { name, pid, id: procIdentity(pid) } : undefined; save(); },
  };
  let stopping = false, exitCode = 0;
  let wake!: () => void;
  const stopRequested = new Promise<void>((r) => { wake = r; });
  // Asking to stop ends the step in progress (its whole process group); the start sequence then unwinds and the shutdown
  // below runs once, after it, so a server that step started is stopped too.
  const requestStop = (code: number) => {
    if (stopping) return;
    stopping = true; exitCode = code; steps.cancelled = true;
    const p = steps.current;
    if (p?.pid) {
      // pg_ctl start hands the server to a child in a session of its own, so ending pg_ctl early would let a server come up
      // after the shutdown looked for it. It is left to finish (its -t 60 bounds it); every other step is asked to end.
      const pgCtl = state.step?.name === "pg_ctl";
      if (!pgCtl) group(p.pid, "SIGTERM");
      // A step that ignores SIGTERM, or is suspended, would hold the shutdown and the lock forever.
      setTimeout(() => { if (steps.current === p) group(p.pid!, "SIGKILL"); }, pgCtl ? 70_000 : 10_000).unref();
    }
    wake();
  };
  const onSignal = () => requestStop(0);
  void held.lost.then(() => {
    if (stopping) return;
    emit({ event: "error", code: "start_failed", message: "the lock helper exited, so this data folder is no longer protected; stopping" });
    requestStop(1);
  });
  process.once("SIGTERM", onSignal);
  process.once("SIGINT", onSignal);

  const spawnService = (name: ProcessName, cmd: string, args: string[], cwd: string, env: Record<string, string>) => {
    const fd = openSync(join(lay.logs, `${name}.log`), "a", 0o600);
    const p = spawn(cmd, args, { cwd, env, detached: true, stdio: ["ignore", fd, fd] });
    closeSync(fd);
    procs.set(name, p);
    if (p.pid) { state.services[name] = p.pid; state.serviceIds![name] = procIdentity(p.pid); save(); }
    // A binary that is missing or cannot run fails here, often with no exit event after it.
    p.once("error", (e) => {
      if (stopping) return;
      emit({ event: "error", code: "start_failed", message: mask(`${name} could not start: ${e.message}`) });
      requestStop(1);
    });
    p.once("exit", (code, sig) => {
      if (stopping) return;
      emit({ event: "service_exited", service: name, code, signal: sig });
      requestStop(3);
    });
  };

  try {
    await reapLeftovers(dataDir, lay, previous, false);
    delete state.leftover; save();
    if (stopping) throw new Cancelled();
    const raw = readJson<unknown>(join(dataDir, "ports.json"));
    const saved = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Partial<Ports>) : undefined;
    // Without the saved web port an existing database would get a new one its stored addresses do not match.
    const hasCluster = existsSync(join(lay.pgData, "PG_VERSION"));
    if (hasCluster && !(Number.isInteger(saved?.api) && saved!.api! >= 1 && saved!.api! <= 65535)) {
      throw new Error(`${join(dataDir, "ports.json")} has no web port, which the existing database's stored addresses use. Restore it from a backup.`);
    }
    secrets = loadSecrets(dataDir, nonEmpty(lay.pgData));
    const ports = await pickPorts(saved, o.portBase);
    // The API's address is stored in the database's settings on its first start (auth, storage and other endpoints), so it
    // must not move for the life of the data folder: a taken port is reported instead of quietly replaced. Decided from the
    // same probe that picked the ports, before ports.json is written.
    if (hasCluster && ports.api !== saved!.api) {
      throw new StartError("port_in_use", `port ${saved!.api} on 127.0.0.1 is in use by another program; PipesHub's stored addresses use it. Stop that program and start again.`);
    }
    writeJson(join(dataDir, "ports.json"), ports);
    const c: Ctx = { layout: lay, ports, secrets };
    state.ports = ports; state.url = apiUrl(c); save();
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
    if (!(e instanceof Cancelled) && !stopping) emit({ event: "error", code: e instanceof StartError ? e.code : "start_failed", message: mask((e as Error).message) });
    requestStop(stopping ? exitCode : 1);
  }
  await stopRequested;

  // The phase is only for status: a failed write (a full disk) must not keep the processes below running.
  state.phase = "stopping"; try { save(); } catch { /* best effort */ }
  emit({ event: "stopping" });
  const pids = [...procs.values()].map((p) => p.pid).filter((p): p is number => !!p);
  pids.forEach((p) => group(p, "SIGTERM"));
  const until = Date.now() + (o.stopWaitS ?? 30) * 1000;
  while (Date.now() < until && pids.some((p) => alive(p))) await sleep(200);
  let failed: Error | undefined;
  try { await killGroups(pids); } catch (e) { failed = e as Error; }
  try { await stopPostgres(lay); } catch (e) { failed ??= e as Error; }
  // On failure the state stays, so the next start or stop ends what is left.
  if (failed) { emit({ event: "error", code: "stop_failed", message: failed.message }); exitCode = 1; }
  // A crashed launcher's processes that this start could not end stay on record for the next start or stop.
  else if (!state.leftover) rmSync(statePath(dataDir), { force: true });
  stopping = true;
  held.release();
  process.off("SIGTERM", onSignal); process.off("SIGINT", onSignal);
  emit({ event: "stopped" });
  return exitCode;
}

/** Processes a crashed launcher left behind: their groups are ended, so the new run owns every port and the database. */
/** Call with the data folder's lock held, so it never acts on a successor's processes. */
export async function reapLeftovers(dataDir: string, lay: Layout, s: State | undefined = readState(dataDir), removeState = true): Promise<void> {
  if (s && sameProcess(s.pid, s.pidId)) return;
  // A launcher that crashed while cleaning up after another kept that one's state: end those processes too.
  if (s?.leftover) await reapLeftovers(dataDir, lay, s.leftover, false);
  // Processes recorded in an earlier boot are all gone; their pids may belong to anything now.
  const sameBoot = !!s && (s.bootId === undefined || s.bootId === bootId());
  // A step (initdb, pg_ctl, a schema step) runs in its own group and outlives a killed launcher: end it before anything
  // else touches the data folder, or a new initdb or a late pg_ctl start would run alongside it.
  const step = s?.step;
  if (sameBoot && step?.pid && ownGroupAlive(step.pid, step.id)) {
    // A pg_ctl start is left to finish (its -t 60 bounds it): its server, started in a session of its own, would otherwise come
    // up after the stopPostgres below looked for it.
    const pgCtl = step.name === "pg_ctl";
    if (!pgCtl) group(step.pid, "SIGTERM");
    const until = Date.now() + (pgCtl ? 70_000 : 10_000);
    while (groupAlive(step.pid) && Date.now() < until) await sleep(100);
    await killGroups([step.pid]);
  }
  // A service's leader may have exited while its workers live on: the group is what counts.
  if (sameBoot) {
    await killGroups(Object.entries(s?.services ?? {}).filter(([name, pid]) => pid && ownGroupAlive(pid, s?.serviceIds?.[name as ProcessName])).map(([, pid]) => pid!));
  }
  // The server is not one of the services: pg_ctl started it, so it outlives a killed launcher.
  await stopPostgres(lay);
  if (removeState) rmSync(statePath(dataDir), { force: true });
}

export async function stop(o: Options, emit: Emit): Promise<number> {
  const { dataDir, lay } = resolve(o);
  // A data folder that was never created has nothing running (a permission error still fails below).
  if (!statSync(dataDir, { throwIfNoEntry: false })) { emit({ event: "stopped", was_running: false }); return 0; }
  const until = Date.now() + 30_000;
  for (;;) {
    const s = running(dataDir);
    if (s) return stopPid(s.pid, dataDir, lay, o, emit);
    // No launcher: clean up what a crashed one left, holding the lock so a start cannot begin meanwhile.
    let lock: Lock | "busy";
    try { lock = await acquireLock(dataDir, lay.python); } catch (e) {
      emit({ event: "error", code: "stop_failed", message: (e as Error).message }); return 1;
    }
    if (lock !== "busy") {
      try { await reapLeftovers(dataDir, lay); } catch (e) {
        emit({ event: "error", code: "stop_failed", message: (e as Error).message }); return 1;
      } finally { lock.release(); }
      emit({ event: "stopped", was_running: false }); return 0;
    }
    // A start holds the lock but has not written its state yet: wait for it, then stop it.
    if (Date.now() > until) { emit({ event: "error", code: "stop_failed", message: "a start is in progress and did not report itself" }); return 1; }
    await sleep(200);
  }
}

// Long enough for a launcher that is still cleaning up a crashed run (a leftover step: up to 70 s; pg_ctl stop: up to 60 s) to
// finish that cleanup, see the signal, and stop its own services (stopWaitS).
const launcherStopMs = (o: Options) => ((o.stopWaitS ?? 30) + 140) * 1000;

async function stopPid(pid: number, dataDir: string, lay: Layout, o: Options, emit: Emit): Promise<number> {
  try { process.kill(pid, "SIGTERM"); } catch { /* already gone */ }
  const until = Date.now() + launcherStopMs(o);
  while (alive(pid) && Date.now() < until) await sleep(200);
  if (alive(pid)) { emit({ event: "error", code: "stop_failed", message: `launcher ${pid} did not stop` }); return 1; }
  // If the launcher died rather than stopped, its step or server may still be up. Under the lock, and only if no new start
  // has taken it meanwhile: that start owns the data folder now. The dead launcher's lock helper can hold the lock a moment
  // longer, so "busy" alone proves neither.
  const lockUntil = Date.now() + 15_000;
  for (;;) {
    let lock: Lock | "busy";
    try { lock = await acquireLock(dataDir, lay.python); } catch (e) {
      emit({ event: "error", code: "stop_failed", message: (e as Error).message }); return 1;
    }
    if (lock !== "busy") {
      try { await reapLeftovers(dataDir, lay); } catch (e) {
        emit({ event: "error", code: "stop_failed", message: (e as Error).message }); return 1;
      } finally { lock.release(); }
      break;
    }
    const next = running(dataDir);
    if (next && next.pid !== pid) break;
    if (Date.now() > lockUntil) { emit({ event: "error", code: "stop_failed", message: `launcher ${pid} stopped, but ${lockPath(dataDir)} is still held` }); return 1; }
    await sleep(200);
  }
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
  let api = false;
  try { api = (await fetchFn(`http://${HOST}:${(st.body.ports as Ports).api}/api/v1/health`, { signal: AbortSignal.timeout(3000) })).status === 200; } catch { /* down */ }
  // Read after the request: the launcher or a service may have stopped while it was pending.
  const now = status(o);
  const services = (now.body.services ?? {}) as Record<string, { alive: boolean }>;
  const checks: Record<string, boolean> = { ...Object.fromEntries(Object.entries(services).map(([n, x]) => [n, x.alive])), api, pg: !!postmasterPid(resolve(o).lay) };
  const healthy = now.body.pid === st.body.pid && now.body.phase === "ready" && Object.values(checks).every(Boolean);
  return { code: healthy ? 0 : 1, body: { healthy, phase: now.body.phase, url: st.body.url, checks } };
}

/** `start --detach`: run the launcher in the background and return once it is ready (or failed, with its own error). */
export async function startDetached(o: Options, emit: Emit, args: string[], selfCmd: string[]): Promise<number> {
  const { dataDir, lay } = resolve(o);
  mkdirSync(lay.logs, { recursive: true, mode: 0o700 });
  const other = running(dataDir);
  if (other) {
    emit({ event: "error", code: "already_running", message: `already running (pid ${other.pid})`, phase: other.phase });
    return 1;
  }
  // Appended, not truncated: a second start racing this one must not wipe its log; this run's lines start at `from`.
  const log = join(lay.logs, "launcher.log"), fd = openSync(log, "a", 0o600);
  const from = statSync(log).size;
  const p = spawn(selfCmd[0]!, [...selfCmd.slice(1), "start", "--json", ...args], { detached: true, stdio: ["ignore", fd, fd] });
  p.unref(); closeSync(fd);
  const until = Date.now() + ((o.healthTimeoutS ?? 300) + 120) * 1000;
  let exited = false; p.once("exit", () => { exited = true; });
  let spawnError: Error | undefined;
  p.once("error", (e) => { spawnError = e; exited = true; });   // could not run at all: without a listener this would throw
  for (;;) {
    const s = readState(dataDir);
    if (s && s.pid === p.pid && s.phase === "ready") { emit({ event: "ready", url: s.url, ports: s.ports, healthy_after_s: s.healthyAfterS, pid: s.pid, data_dir: dataDir }); return 0; }
    if (exited || Date.now() >= until) break;
    await sleep(300);
  }
  // Out of time: this start is cancelled (it stops what it started), so a "not ready" answer leaves nothing coming up later.
  if (!exited && p.pid) {
    try { process.kill(p.pid, "SIGTERM"); } catch { /* exiting */ }
    const end = Date.now() + launcherStopMs(o);
    while (!exited && Date.now() < end) await sleep(300);
  }
  if (spawnError) { emit({ event: "error", code: "start_failed", message: `the launcher could not be started: ${spawnError.message}` }); return 1; }
  // from counts bytes: cut the bytes, then decode, or an earlier non-ASCII line shifts the cut
  const mine = (() => { try { return readFileSync(log).subarray(from).toString("utf8"); } catch { return ""; } })();
  // The background launcher's own error (already_running, start_failed, ...) says more than "not ready".
  const own = mine.split("\n").reverse().map((l) => { try { return JSON.parse(l) as Record<string, unknown>; } catch { return null; } })
    .find((e) => e?.event === "error");
  emit(own ?? { event: "error", code: "not_ready", message: `did not become ready; see ${log}`, log: mine.slice(-2000) });
  return 1;
}
