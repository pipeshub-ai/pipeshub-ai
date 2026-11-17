// Everything the launcher decides before it starts a process: where things live, which ports, which secrets, and the exact
// environment each process gets. The environment follows docker-compose.postgres-only.yml and process_monitor.sh (one login
// per service), with every address on loopback. Pure functions where possible, so the unit tests can pin them.
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import net from "node:net";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

/** The Python services process_monitor.sh starts by default (parsing and extraction only run with USE_PARSING_SERVICE=true). */
export const PY_SERVICES = ["query", "connectors", "indexing", "embedding", "docling"] as const;
export type PyService = (typeof PY_SERVICES)[number];
export const SERVICES = ["api", "pg", ...PY_SERVICES] as const;
export type ServiceName = (typeof SERVICES)[number];
export type Ports = Record<ServiceName, number>;
export type ProcessName = "node" | PyService | "placement" | "fold";

export const HOST = "127.0.0.1";

export function defaultDataDir(env: NodeJS.ProcessEnv = process.env, platform = process.platform): string {
  const home = env.HOME || homedir();
  if (platform === "darwin") return join(home, "Library", "Application Support", "PipesHub");
  return join(env.XDG_DATA_HOME || join(home, ".local", "share"), "pipeshub");
}

export interface Layout {
  bundle: string;
  dataDir: string;
  node: string; // the Node runtime
  python: string;
  pgBin: string;
  appDir: string; // cwd of the schema tools (holds db/)
  backendDir: string; // cwd of the API (dist/index.js, dist/public, src/ templates), as in the image
  pythonDir: string; // cwd of the Python services (holds app/)
  logs: string;
  home: string; // HOME of every process: model caches and the like land in the data folder, never in the bundle
  pgData: string;
  run: string; // the PostgreSQL socket folder
}

export function layout(bundle: string, dataDir: string): Layout {
  return {
    bundle, dataDir,
    node: join(bundle, "node", "bin", "node"),
    python: join(bundle, "py", "bin", "python3"),
    pgBin: join(bundle, "pg", "bin"),
    appDir: join(bundle, "app"),
    backendDir: join(bundle, "app", "backend"),
    pythonDir: join(bundle, "app", "python"),
    logs: join(dataDir, "logs"),
    home: join(dataDir, "home"),
    pgData: join(dataDir, "pgdata"),
    run: join(dataDir, "run"),
  };
}

// ---- ports ------------------------------------------------------------------------------------------------------------------

export function isFree(port: number, host = HOST): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once("error", () => resolve(false));
    s.listen(port, host, () => s.close(() => resolve(true)));
  });
}

function anyFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once("error", reject);
    s.listen(0, HOST, () => {
      const port = (s.address() as net.AddressInfo).port;
      s.close(() => resolve(port));
    });
  });
}

/**
 * One free loopback port per service. A port from the previous run is kept while it is free (the web address, and any OAuth
 * redirect registered against it, stays the same); otherwise the next free port at or above `base` when given, else one the
 * operating system picks.
 */
export async function pickPorts(saved: Partial<Ports> = {}, base?: number, isFreeFn = isFree): Promise<Ports> {
  const used = new Set<number>();
  const ports = {} as Ports;
  for (const name of SERVICES) {
    let port = saved[name];
    if (!port || used.has(port) || !(await isFreeFn(port))) {
      port = undefined;
      if (base) for (let p = base; p < 65535 && !port; p++) if (!used.has(p) && (await isFreeFn(p))) port = p;
      port ??= await anyFreePort();
    }
    used.add(port);
    ports[name] = port;
  }
  return ports;
}

// ---- secrets and small json files ------------------------------------------------------------------------------------------

/** The PostgreSQL logins bootstrap_roles creates (plus the administrator), one password each. */
export const PG_LOGINS = ["admin", "node", "connectors", "indexing", "query", "kv", "jobs", "migrate", "backup"] as const;
export const APP_SECRETS = ["secret_key", "jwt_secret", "scoped_jwt_secret", "cookie_secret"] as const;
export type Secrets = Record<`pg_${(typeof PG_LOGINS)[number]}` | (typeof APP_SECRETS)[number], string>;

export function writeJson(path: string, value: unknown, mode = 0o600): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(value, null, 2), { mode });
  chmodSync(tmp, mode);
  renameSync(tmp, path);
}

export function readJson<T>(path: string): T | undefined {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    return undefined;
  }
}

/** Generated once and kept 0600 in the data folder; a missing key (an older file) is filled in, an existing one never changes. */
export function loadSecrets(dataDir: string): Secrets {
  const path = join(dataDir, "secrets.json");
  const have = (existsSync(path) ? readJson<Partial<Secrets>>(path) : undefined) ?? {};
  const names = [...PG_LOGINS.map((r) => `pg_${r}`), ...APP_SECRETS] as (keyof Secrets)[];
  let changed = false;
  for (const n of names) if (!have[n]) { have[n] = randomBytes(n.startsWith("pg_") ? 24 : 32).toString("hex"); changed = true; }
  if (changed) writeJson(path, have);
  else chmodSync(path, 0o600);
  return have as Secrets;
}

/** Every secret value replaced by ***, for text that leaves the launcher (an error message quoting a log). */
export function redact(text: string, s: Secrets): string {
  return Object.values(s).reduce((t, v) => t.split(v).join("***"), text);
}

// ---- environments -----------------------------------------------------------------------------------------------------------

export interface Ctx {
  layout: Layout;
  ports: Ports;
  secrets: Secrets;
  host?: NodeJS.ProcessEnv; // the launcher's own environment: only a few variables are passed on
}

const dsn = (c: Ctx, user: string, password: string) => `postgresql://${user}:${password}@${HOST}:${c.ports.pg}/pipeshub`;
const url = (port: number) => `http://${HOST}:${port}`;

// 127.0.0.1, not localhost: the services listen on IPv4 loopback only and "localhost" may resolve to ::1 first. The web address,
// the public URL the API builds links from and the browser's origin must be one origin (cookies are per host).
export const apiUrl = (c: Pick<Ctx, "ports">) => url(c.ports.api);

/** What every process may see besides its own credentials. */
function common(c: Ctx): Record<string, string> {
  const h = c.host ?? process.env;
  const pass = Object.fromEntries(["TMPDIR", "LOG_LEVEL", "TELEMETRY_COLLECTION_FLAG"].flatMap((k) => (h[k] ? [[k, h[k]!]] : [])));
  return {
    PATH: h.PATH ?? "/usr/bin:/bin", HOME: c.layout.home, LANG: "C.UTF-8", ...pass,
    NODE_ENV: "production", PYTHONUNBUFFERED: "1", PYTHONDONTWRITEBYTECODE: "1",
    FRONTEND_PUBLIC_URL: apiUrl(c), ALLOWED_ORIGINS: `http://localhost:${c.ports.api},${apiUrl(c)}`,
    QUERY_BACKEND: url(c.ports.query), CONNECTOR_BACKEND: url(c.ports.connectors), INDEXING_BACKEND: url(c.ports.indexing),
    EMBEDDING_SERVER_URL: url(c.ports.embedding), DOCLING_BACKEND: url(c.ports.docling), DOCLING_SERVICE_URL: url(c.ports.docling),
    DATA_STORE: "postgres", DOC_STORE: "postgres", VECTOR_DB_TYPE: "pgvector", KV_STORE_TYPE: "postgres", MESSAGE_BROKER: "postgres",
    REDIS_MODE: "postgres", CRAWL_SCHEDULER: "postgres", PG_POOLER: "none", PGVECTOR_POOLER: "none",
    PIPESHUB_BIND_HOST: HOST,
  };
}

const appSecrets = (s: Secrets) => ({ SECRET_KEY: s.secret_key, JWT_SECRET: s.jwt_secret, SCOPED_JWT_SECRET: s.scoped_jwt_secret, COOKIE_SECRET: s.cookie_secret });

/** The login each Python service runs as (process_monitor.sh use_own_pg_login): its own, or the key-value one for the config-only services. */
const LOGIN: Record<PyService, "query" | "connectors" | "indexing" | "kv"> = {
  query: "query", connectors: "connectors", indexing: "indexing", embedding: "kv", docling: "kv",
};

/** The environment of each long-running process. No process sees another service's login, the administrator's or (except placement) the migrate one. */
export function serviceEnvs(c: Ctx): Record<ProcessName, Record<string, string>> {
  const s = c.secrets, base = common(c);
  const jobs = dsn(c, "pipeshub_jobs", s.pg_jobs);
  const kv = dsn(c, "pipeshub_kv_login", s.pg_kv);
  const shared = { ...base, ...appSecrets(s), PG_JOBS_URL: jobs, PGVECTOR_JOBS_DSN: jobs, PG_LISTEN_URL: kv };
  const py = {} as Record<PyService, Record<string, string>>;
  for (const svc of PY_SERVICES) {
    const role = LOGIN[svc];
    const own = dsn(c, `pipeshub_${role}_login`, s[`pg_${role}`]);
    py[svc] = { ...shared, PIPESHUB_SERVICE: svc, PG_RUNTIME_SERVICE: role, PG_CORE_URL: own, PGVECTOR_DSN: own, PIPESHUB_BIND_PORT: String(c.ports[svc]) };
  }
  const migrate = `postgresql+psycopg://pipeshub_migrate:${s.pg_migrate}@${HOST}:${c.ports.pg}/pipeshub`;
  return {
    node: { ...shared, PORT: String(c.ports.api), PG_NODE_URL: dsn(c, "pipeshub_node_login", s.pg_node), PIPESHUB_SKIP_SLACKBOT: "true" },
    ...py,
    placement: { ...base, PG_MIGRATE_URL: migrate, PG_JOBS_URL: jobs },
    fold: { ...base, PG_JOBS_URL: jobs },
  };
}

/** The one-shot schema steps: the administrator login lives here and nowhere else. */
export function schemaEnv(c: Ctx): { createdb: Record<string, string>; bootstrap: Record<string, string>; migrate: Record<string, string> } {
  const s = c.secrets, h = c.host ?? process.env;
  const plain = { PATH: h.PATH ?? "/usr/bin:/bin", HOME: c.layout.home, LANG: "C.UTF-8", PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1" };
  const passwords = Object.fromEntries(PG_LOGINS.filter((r) => r !== "admin").map((r) => [`PG_${r.toUpperCase()}_PASSWORD`, s[`pg_${r}`]]));
  return {
    createdb: { ...plain, PGHOST: HOST, PGPORT: String(c.ports.pg), PGUSER: "postgres", PGPASSWORD: s.pg_admin },
    bootstrap: { ...plain, PG_ADMIN_URL: dsn(c, "postgres", s.pg_admin), PG_BOOTSTRAP_EXTENSIONS: "vector,pg_trgm", ...passwords },
    migrate: { ...plain, PG_MIGRATE_URL: `postgresql+psycopg://pipeshub_migrate:${s.pg_migrate}@${HOST}:${c.ports.pg}/pipeshub` },
  };
}

// sun_path is 104 bytes on macOS and 108 on Linux, and PostgreSQL appends "/.s.PGSQL.<port>" to the folder.
const SOCKET_PATH_MAX: Partial<Record<NodeJS.Platform, number>> = { darwin: 103, linux: 107 };

/** The socket folder, or "" (TCP only) where the platform has no unix sockets or the path would be too long. */
export function socketDir(c: Ctx, platform = process.platform): string {
  const max = SOCKET_PATH_MAX[platform];
  return max && Buffer.byteLength(`${c.layout.run}/.s.PGSQL.${c.ports.pg}`) <= max ? c.layout.run : "";
}

const quote = (v: string) => `'${v.replace(/'/g, "''")}'`;

/**
 * The server settings, written to <pgdata>/launcher.conf on every start (postgresql.conf includes it): loopback TCP, an
 * owner-only socket where it fits, the Compose image's preload list, laptop sizing. A file, not pg_ctl -o, so a data folder
 * with spaces in its path ("Application Support") needs no shell quoting.
 */
export function postgresConf(c: Ctx, h: NodeJS.ProcessEnv = process.env, platform = process.platform): string {
  const settings: [string, string][] = [
    ["port", String(c.ports.pg)], ["listen_addresses", HOST],
    ["unix_socket_directories", socketDir(c, platform)], ["unix_socket_permissions", "0700"],
    ["shared_preload_libraries", "pg_stat_statements,auto_explain,pg_textsearch"],
    ["max_connections", h.PG_MAX_CONNECTIONS ?? "120"], ["shared_buffers", h.PG_SHARED_BUFFERS ?? "128MB"],
  ];
  return settings.map(([k, v]) => `${k} = ${quote(v)}`).join("\n") + "\n";
}
