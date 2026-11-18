import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdtempSync, readFileSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "node:test";
import {
  type Ctx, PY_SERVICES, SERVICES, defaultDataDir, layout, loadSecrets, pickPorts, postgresConf, redact, schemaEnv, serviceEnvs, socketDir, writeJson,
} from "../src/config.ts";

const tmp = () => mkdtempSync(join(tmpdir(), "psh-cfg-"));
const ports = Object.fromEntries(SERVICES.map((n, i) => [n, 50000 + i])) as Ctx["ports"];
const ctx = (dataDir = "/d"): Ctx => ({ layout: layout("/b", dataDir), ports, secrets: loadSecrets(tmp()), host: { PATH: "/usr/bin" } });

describe("data folder", () => {
  test("per platform", () => {
    assert.equal(defaultDataDir({ HOME: "/Users/a" }, "darwin"), "/Users/a/Library/Application Support/PipesHub");
    assert.equal(defaultDataDir({ HOME: "/home/a" }, "linux"), "/home/a/.local/share/pipeshub");
    assert.equal(defaultDataDir({ HOME: "/home/a", XDG_DATA_HOME: "/x" }, "linux"), "/x/pipeshub");
  });
});

describe("ports", () => {
  test("distinct and free", async () => {
    const p = await pickPorts();
    assert.equal(new Set(Object.values(p)).size, SERVICES.length);
  });
  test("a free port from the last run is kept, a taken one is replaced", async () => {
    const taken = net.createServer();
    await new Promise<void>((r) => taken.listen(0, "127.0.0.1", r));
    try {
      const busy = (taken.address() as net.AddressInfo).port;
      const first = await pickPorts();
      const again = await pickPorts({ ...first, query: busy });
      assert.equal(again.api, first.api);
      assert.equal(again.pg, first.pg);
      assert.notEqual(again.query, busy);
    } finally { taken.close(); }
  });
  test("a base scans upward from it", async () => {
    const p = await pickPorts({}, 54800, async () => true);
    assert.deepEqual(Object.values(p), SERVICES.map((_, i) => 54800 + i));
    const top = await pickPorts({}, 65536 - SERVICES.length, async () => true, async () => 1);
    assert.deepEqual(Object.values(top), SERVICES.map((_, i) => 65536 - SERVICES.length + i), "65535 is a port too");
  });
  test("a saved port out of range is replaced, not probed", async () => {
    const p = await pickPorts({ query: 70000, pg: 1.5 } as never);
    assert.ok(Number.isInteger(p.query) && p.query <= 65535 && Number.isInteger(p.pg));
  });
});

describe("ports, ephemeral", () => {
  test("an OS-picked port already handed out in this run is not handed out again", async () => {
    const picks = [41000, 41000, 41000, 41001, 41002, 41003, 41004, 41005, 41006, 41007];
    const p = await pickPorts({}, undefined, async () => false, async () => picks.shift()!);
    assert.equal(new Set(Object.values(p)).size, SERVICES.length);
  });
});

describe("secrets, damaged", () => {
  test("an unreadable or malformed secrets file is left as it is and the error says so", () => {
    const d = tmp();
    writeFileSync(join(d, "secrets.json"), "[]", { mode: 0o600 });
    assert.throws(() => loadSecrets(d), /secrets\.json/);
    assert.equal(readFileSync(join(d, "secrets.json"), "utf8"), "[]");
    writeFileSync(join(d, "secrets.json"), "{oops", { mode: 0o600 });
    assert.throws(() => loadSecrets(d), /secrets\.json/);
    assert.equal(readFileSync(join(d, "secrets.json"), "utf8"), "{oops");
  });
  test("the error never quotes the file's text", () => {
    const d = tmp();
    writeFileSync(join(d, "secrets.json"), '{"pg_admin": abcd1234secret}', { mode: 0o600 });
    assert.throws(() => loadSecrets(d), (e: Error) => /secrets\.json/.test(e.message) && !e.message.includes("abcd1234"));   // Node quotes about 10 characters around the error
  });
  test("once the database exists, a missing administrator password is never generated", () => {
    const d = tmp();
    assert.throws(() => loadSecrets(d, true), /pg_admin/);
    assert.ok(!existsSync(join(d, "secrets.json")));
    const raw = JSON.stringify({ ...loadSecrets(tmp()), pg_admin: undefined });
    writeFileSync(join(d, "secrets.json"), raw, { mode: 0o600 });
    assert.throws(() => loadSecrets(d, true), /pg_admin/);
    assert.equal(readFileSync(join(d, "secrets.json"), "utf8"), raw);
    assert.ok(loadSecrets(d, false).pg_admin, "a new cluster still gets one");
  });
});

describe("secrets", () => {
  test("generated once, 0600, distinct and stable; a missing key is filled in, the others never change", () => {
    const d = tmp();
    const a = loadSecrets(d);
    assert.equal(statSync(join(d, "secrets.json")).mode & 0o777, 0o600);
    assert.deepEqual(loadSecrets(d), a);
    assert.equal(new Set(Object.values(a)).size, Object.keys(a).length);
    assert.ok(a.pg_backup, "bootstrap_roles refuses to create the backup login without a password");
    const raw = JSON.parse(readFileSync(join(d, "secrets.json"), "utf8"));
    delete raw.pg_kv;
    writeFileSync(join(d, "secrets.json"), JSON.stringify(raw));
    chmodSync(join(d, "secrets.json"), 0o644);   // writeFileSync's mode applies only to a new file
    assert.equal(statSync(join(d, "secrets.json")).mode & 0o777, 0o644);
    const b = loadSecrets(d);
    assert.equal(b.pg_node, a.pg_node);
    assert.ok(b.pg_kv);
    assert.equal(statSync(join(d, "secrets.json")).mode & 0o777, 0o600);
  });
  test("a complete secrets file that became readable by others is set back to 0600, unchanged", () => {
    const d = tmp();
    const a = loadSecrets(d);
    chmodSync(join(d, "secrets.json"), 0o644);
    assert.equal(statSync(join(d, "secrets.json")).mode & 0o777, 0o644);
    assert.deepEqual(loadSecrets(d), a);
    assert.equal(statSync(join(d, "secrets.json")).mode & 0o777, 0o600);
  });
  test("a file left at the temporary name, or a symlink there, is never written through", () => {
    const d = tmp(), elsewhere = join(tmp(), "other");
    writeFileSync(elsewhere, "untouched");
    symlinkSync(elsewhere, join(d, `secrets.json.${process.pid}.tmp`));
    writeJson(join(d, "secrets.json"), { pg_admin: "x" });
    assert.equal(readFileSync(elsewhere, "utf8"), "untouched");
    assert.deepEqual(JSON.parse(readFileSync(join(d, "secrets.json"), "utf8")), { pg_admin: "x" });
    assert.equal(statSync(join(d, "secrets.json")).mode & 0o777, 0o600);
  });
  test("redact masks every secret value", () => {
    const s = ctx().secrets;
    assert.equal(redact(`url postgresql://x:${s.pg_jobs}@h and ${s.secret_key}`, s), "url postgresql://x:***@h and ***");
  });
});

describe("environments", () => {
  const c = ctx();
  const envs = serviceEnvs(c);
  const s = c.secrets;
  test("each Python service logs in as its own role, as process_monitor.sh does; embedding and docling as the key-value one", () => {
    for (const svc of ["query", "connectors", "indexing"] as const) {
      assert.match(envs[svc].PG_CORE_URL!, new RegExp(`^postgresql://pipeshub_${svc}_login:${s[`pg_${svc}`]}@127\\.0\\.0\\.1:50001/pipeshub$`));
      assert.equal(envs[svc].PGVECTOR_DSN, envs[svc].PG_CORE_URL);
      assert.equal(envs[svc].PG_RUNTIME_SERVICE, svc);
    }
    for (const svc of ["embedding", "docling"] as const) {
      assert.match(envs[svc].PG_CORE_URL!, /^postgresql:\/\/pipeshub_kv_login:/);
      assert.equal(envs[svc].PG_RUNTIME_SERVICE, "kv");
    }
  });
  test("no process sees another service's login, the administrator's, or (except placement) the migrate one", () => {
    const own: Record<string, string[]> = {
      node: [s.pg_node, s.pg_jobs, s.pg_kv], query: [s.pg_query, s.pg_jobs, s.pg_kv], connectors: [s.pg_connectors, s.pg_jobs, s.pg_kv],
      indexing: [s.pg_indexing, s.pg_jobs, s.pg_kv], embedding: [s.pg_kv, s.pg_jobs], docling: [s.pg_kv, s.pg_jobs],
      placement: [s.pg_migrate, s.pg_jobs], fold: [s.pg_jobs],
    };
    const pg = Object.entries(s).filter(([k]) => k.startsWith("pg_")).map(([, v]) => v);
    for (const [name, env] of Object.entries(envs)) {
      const text = JSON.stringify(env);
      for (const v of pg) assert.equal(text.includes(v), own[name]!.includes(v), `${name}: ${pg.indexOf(v)}`);
    }
  });
  test("every address is loopback; each service is told its own port", () => {
    const all = JSON.stringify(envs);
    assert.ok(!all.includes("0.0.0.0"));
    assert.ok(!/@(?!127\.0\.0\.1:)[^"]*\/pipeshub/.test(all));
    for (const svc of PY_SERVICES) {
      assert.equal(envs[svc].PIPESHUB_BIND_HOST, "127.0.0.1");
      assert.equal(envs[svc].PIPESHUB_BIND_PORT, String(ports[svc]));
    }
    assert.equal(envs.node.PORT, String(ports.api));
    assert.equal(envs.node.PIPESHUB_BIND_HOST, "127.0.0.1");
    assert.equal(envs.query.CONNECTOR_BACKEND, `http://127.0.0.1:${ports.connectors}`);
    assert.equal(envs.indexing.DOCLING_SERVICE_URL, `http://127.0.0.1:${ports.docling}`);
    assert.equal(envs.node.FRONTEND_PUBLIC_URL, `http://127.0.0.1:${ports.api}`);
  });
  test("every store runs on PostgreSQL, as docker-compose.postgres-only.yml sets it", () => {
    for (const env of Object.values(envs).slice(0, 6)) {
      assert.deepEqual(
        [env.DATA_STORE, env.DOC_STORE, env.VECTOR_DB_TYPE, env.KV_STORE_TYPE, env.MESSAGE_BROKER, env.REDIS_MODE, env.CRAWL_SCHEDULER],
        ["postgres", "postgres", "pgvector", "postgres", "postgres", "postgres", "postgres"],
      );
    }
  });
  test("processes write nothing into the bundle", () => {
    assert.equal(envs.query.PYTHONDONTWRITEBYTECODE, "1");
    assert.equal(envs.query.HOME, "/d/home");
  });
  test("schema steps: the administrator login only in createdb and the role bootstrap, with every login's password", () => {
    const e = schemaEnv(c);
    assert.match(e.bootstrap.PG_ADMIN_URL!, new RegExp(`^postgresql://postgres:${s.pg_admin}@127\\.0\\.0\\.1:`));
    for (const r of ["NODE", "CONNECTORS", "INDEXING", "QUERY", "KV", "JOBS", "MIGRATE", "BACKUP"]) assert.ok(e.bootstrap[`PG_${r}_PASSWORD`]);
    assert.equal(e.createdb.PGPASSWORD, s.pg_admin);
    // createdb also sets each existing login's password, which bootstrap_roles leaves alone
    for (const r of ["NODE", "CONNECTORS", "INDEXING", "QUERY", "KV", "JOBS", "MIGRATE", "BACKUP"]) {
      assert.equal(e.createdb[`PG_${r}_PASSWORD`], s[`pg_${r.toLowerCase()}` as keyof typeof s]);
    }
    assert.ok(!JSON.stringify(e.migrate).includes(s.pg_admin));
  });
});

describe("postgres settings", () => {
  test("loopback TCP, the picked port, an owner-only socket in the data folder", () => {
    const conf = postgresConf(ctx(), {}, "linux");
    assert.match(conf, /^listen_addresses = '127\.0\.0\.1'$/m);
    assert.match(conf, /^port = '50001'$/m);
    assert.match(conf, /^unix_socket_directories = '"\/d\/run"'$/m);
    assert.match(conf, /^unix_socket_permissions = '0700'$/m);
  });
  test("no socket where the path would be too long or the platform has none; quotes are escaped", () => {
    const long = ctx(`/${"x".repeat(100)}`);
    assert.equal(socketDir(long, "darwin"), "");
    assert.equal(socketDir(ctx(), "win32"), "");
    assert.match(postgresConf(ctx("/Users/o'neil/Library/Application Support/PipesHub"), {}, "darwin"),
      /^unix_socket_directories = '"\/Users\/o''neil\/Library\/Application Support\/PipesHub\/run"'$/m);
    // a comma would split the list in two
    assert.match(postgresConf(ctx("/tmp/psh,with-comma"), {}, "linux"), /^unix_socket_directories = '"\/tmp\/psh,with-comma\/run"'$/m);
    assert.match(postgresConf(long, {}, "darwin"), /^unix_socket_directories = ''$/m);
    // the settings file reads backslash escapes inside quotes
    assert.match(postgresConf(ctx("/tmp/a\\b"), {}, "linux"), /^unix_socket_directories = '"\/tmp\/a\\\\b\/run"'$/m);
  });
});
