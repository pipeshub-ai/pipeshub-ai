// The launcher as a process, against a fake bundle (shell stand-ins for PostgreSQL and Python, a stub API): ordering, the
// ready/stop/status/health contract, a service dying, a failed step, a crashed launcher's leftovers, and no process left after a stop.
import assert from "node:assert/strict";
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, test } from "node:test";
import { alive as aliveFn } from "../src/launcher.ts";

const root = mkdtempSync(join(tmpdir(), "psh-fake-"));
const main = join(import.meta.dirname, "..", "src", "main.ts"), fakeApi = join(import.meta.dirname, "fake-api.mjs");
const sh = (path: string, body: string) => { mkdirSync(join(path, ".."), { recursive: true }); writeFileSync(path, `#!/bin/sh\n${body}\n`); chmodSync(path, 0o755); };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function fakeBundle(): string {
  const b = mkdtempSync(join(root, "bundle-"));
  // initdb -D dir: creates the data folder. pg_ctl start: a live pid in postmaster.pid, after checking the settings file; stop ends it.
  sh(join(b, "pg/bin/initdb"), 'while [ $# -gt 0 ]; do [ "$1" = -D ] && d=$2; shift; done; mkdir -p "$d"; echo 18 > "$d/PG_VERSION"; : > "$d/postgresql.conf"');
  sh(join(b, "pg/bin/pg_ctl"), `while [ $# -gt 0 ]; do case "$1" in -D) d=$2;; start|stop) a=$1;; esac; shift; done
if [ "$a" = start ]; then grep -q "^listen_addresses = '127.0.0.1'" "$d/launcher.conf" || exit 9; grep -q "include 'launcher.conf'" "$d/postgresql.conf" || exit 8
  sleep 600 >/dev/null 2>&1 & echo $! > "$d/postmaster.pid"; else
  [ -f "$d/postmaster.pid" ] && kill $(head -1 "$d/postmaster.pid") 2>/dev/null; rm -f "$d/postmaster.pid"; fi`);
  // Each step and service records its arguments and environment, so the test can check who got which credential.
  sh(join(b, "py/bin/python3"), `name=$(echo "$*" | sed -n 's/.*-m \\([a-z_.]*\\).*/\\1/p'); name=\${name:-createdb}
env > "$HOME/env-$name"; echo "$name" >> "$HOME/order"
case "$*" in
  *app.*_main*) [ -f "$HOME/die-$name" ] && { sleep 1; exit 7; }; exec sleep 600;;
  *db.maintain*) exec sleep 600;;
  *db.bootstrap_roles*) [ -n "$PG_BACKUP_PASSWORD" ] || exit 2;;
esac
exit 0`);
  sh(join(b, "node/bin/node"), `exec "${process.execPath}" "${fakeApi}"`);
  for (const d of ["app/python", "app/backend"]) mkdirSync(join(b, d), { recursive: true });
  return b;
}

const live: ChildProcess[] = [];
let nextBase = 54800;
function launch(b: string, d: string, ...args: string[]) {
  const p = spawn(process.execPath, [main, ...args, "--bundle", b, "--data-dir", d, "--port-base", String(nextBase += 20), "--json"], { stdio: ["ignore", "pipe", "pipe"] });
  live.push(p);
  const events: Record<string, any>[] = [];
  let buf = "";
  p.stdout!.on("data", (chunk) => { buf += chunk; let i; while ((i = buf.indexOf("\n")) >= 0) { events.push(JSON.parse(buf.slice(0, i))); buf = buf.slice(i + 1); } });
  const exited = new Promise<number>((r) => p.once("exit", (c) => r(c ?? -1)));
  const until = async (event: string, ms = 30000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const e = events.find((x) => x.event === event); if (e) return e; await sleep(100); }
    throw new Error(`no '${event}' event; got ${JSON.stringify(events)}`);
  };
  return { p, events, exited, until };
}
// async on purpose: a blocking spawnSync would stop this process reaping the launcher it started, leaving a zombie that still "exists"
const cli = (b: string, d: string, ...args: string[]) => new Promise<{ code: number | null; out: any }>((resolve) => {
  const p = spawn(process.execPath, [main, ...args, "--bundle", b, "--data-dir", d, "--json"], { stdio: ["ignore", "pipe", "pipe"] });
  let text = ""; p.stdout!.on("data", (c) => (text += c));
  p.once("exit", (code) => resolve({ code, out: text.trim() ? JSON.parse(text.trim().split("\n").pop()!) : {} }));
});
const alive = (pid: number) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const envOf = (d: string, name: string) => Object.fromEntries(readFileSync(join(d, "home", `env-${name}`), "utf8").trim().split("\n").map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));

after(() => { for (const p of live) try { p.kill("SIGKILL"); } catch { /* gone */ } rmSync(root, { recursive: true, force: true }); });

describe("alive", () => {
  test("a child that exited but was not reaped is not alive", () => {
    const c = spawn("true");
    spawnSync("sleep", ["0.5"]);                      // blocks this process's loop, so the exited child stays a zombie
    assert.equal(aliveFn(c.pid), false);
    assert.equal(aliveFn(process.pid), true);
    assert.equal(aliveFn(undefined), false);
  });
});

describe("launcher", () => {
  test("cold start: initdb, postgres, schema, services, ready; status and health; stop leaves nothing", async () => {
    const b = fakeBundle(), d = join(root, "data one");
    const l = launch(b, d, "start");
    const ready = await l.until("ready");
    const stages = l.events.filter((e) => e.event === "progress").map((e) => e.stage);
    assert.deepEqual(stages, ["initdb", "postgres", "schema", "services", "health"]);
    assert.equal(ready.url, `http://127.0.0.1:${ready.ports.api}`);
    const order = readFileSync(join(d, "home", "order"), "utf8").trim().split("\n");
    assert.deepEqual(order.slice(0, 3), ["createdb", "db.bootstrap_roles", "db.migrate"]);
    assert.deepEqual(order.slice(3).sort(), ["app.connectors_main", "app.docling_main", "app.embedding_main", "app.indexing_main", "app.query_main", "db.maintain", "db.maintain"].sort());
    // each service runs as its own login on its own loopback port; the administrator password reaches only createdb and the role bootstrap
    const secrets = JSON.parse(readFileSync(join(d, "secrets.json"), "utf8"));
    assert.equal(statSync(join(d, "secrets.json")).mode & 0o777, 0o600);
    assert.equal(statSync(d).mode & 0o777, 0o700);
    const q = envOf(d, "app.query_main");
    assert.match(q.PG_CORE_URL!, /^postgresql:\/\/pipeshub_query_login:/);
    assert.equal(q.PIPESHUB_BIND_HOST, "127.0.0.1");
    assert.equal(q.PIPESHUB_BIND_PORT, String(ready.ports.query));
    for (const f of readdirSync(join(d, "home")).filter((n) => n.startsWith("env-"))) {
      const has = readFileSync(join(d, "home", f), "utf8").includes(secrets.pg_admin);
      assert.equal(has, f === "env-createdb" || f === "env-db.bootstrap_roles", f);
    }
    // no secret in any log or event
    const logs = readdirSync(join(d, "logs")).map((f) => readFileSync(join(d, "logs", f), "utf8")).join("\n") + JSON.stringify(l.events);
    for (const v of Object.values(secrets) as string[]) assert.ok(!logs.includes(v));

    const st = await cli(b, d, "status");
    assert.equal(st.code, 0);
    assert.equal(st.out.phase, "ready");
    const pids = Object.values(st.out.services as Record<string, { pid: number }>).map((s) => s.pid);
    assert.equal(pids.length, 8);
    const h = await cli(b, d, "health");
    assert.equal(h.code, 0);
    assert.equal(h.out.healthy, true);
    assert.equal((await fetch(`http://127.0.0.1:${ready.ports.api}/api/v1/health`)).status, 200);
    const pg = Number(readFileSync(join(d, "pgdata/postmaster.pid"), "utf8").split("\n")[0]);
    assert.ok(alive(pg));

    const stop = await cli(b, d, "stop");
    assert.equal(stop.code, 0);
    assert.equal(await l.exited, 0);
    assert.ok(!pids.some(alive));
    assert.ok(!alive(pg));
    assert.ok(!existsSync(join(d, "launcher.json")));
    assert.equal((await cli(b, d, "status")).code, 3);
    assert.equal((await cli(b, d, "health")).code, 1);
    assert.deepEqual(await cli(b, d, "stop"), { code: 0, out: { event: "stopped", was_running: false } });
  });

  test("warm start reuses the data folder and the web port; a second start is refused", async () => {
    const b = fakeBundle(), d = join(root, "data two");
    const a = launch(b, d, "start");
    const r1 = await a.until("ready");
    const dup = launch(b, d, "start");
    assert.equal(await dup.exited, 1);
    assert.equal(dup.events.find((e) => e.event === "error")?.code, "already_running");
    await cli(b, d, "stop"); await a.exited;
    const w = launch(b, d, "start");
    const r2 = await w.until("ready");
    assert.ok(!w.events.some((e) => e.stage === "initdb"));
    assert.equal(r2.ports.api, r1.ports.api);
    w.p.kill("SIGTERM");
    assert.equal(await w.exited, 0);
  });

  test("a service that dies stops the rest and exits 3", async () => {
    const b = fakeBundle(), d = join(root, "data three");
    mkdirSync(join(d, "home"), { recursive: true }); writeFileSync(join(d, "home", "die-app.indexing_main"), "");
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 3);
    const died = l.events.find((e) => e.event === "service_exited");
    assert.equal(died?.service, "indexing");
    assert.equal(died?.code, 7);
    assert.ok(!existsSync(join(d, "pgdata/postmaster.pid")));
  });

  test("a start-up step that fails reports its log, with secrets masked, and exits 1", async () => {
    const b = fakeBundle(), d = join(root, "data four");
    sh(join(b, "py/bin/python3"), 'echo "boom: $PG_MIGRATE_URL"; exit 1');
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    const err = l.events.find((e) => e.event === "error");
    assert.equal(err?.code, "start_failed");
    assert.match(err?.message, /createdb failed/);
    const secrets = JSON.parse(readFileSync(join(d, "secrets.json"), "utf8"));
    assert.ok(!JSON.stringify(l.events).includes(secrets.pg_admin));
    assert.ok(!existsSync(join(d, "pgdata/postmaster.pid")));
  });

  test("a failing migration stops the start before any service runs", async () => {
    const b = fakeBundle(), d = join(root, "data five");
    sh(join(b, "py/bin/python3"), `case "$*" in *db.migrate*) echo "migration failed: password=$PG_MIGRATE_URL"; exit 1;; *app.*|*db.maintain*) touch "$HOME/started"; exec sleep 600;; esac; exit 0`);
    mkdirSync(join(d, "home"), { recursive: true });
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    const err = l.events.find((e) => e.event === "error");
    assert.match(err?.message, /migrate failed/);
    const secrets = JSON.parse(readFileSync(join(d, "secrets.json"), "utf8"));
    assert.ok(err?.message.includes("***"));
    assert.ok(!err?.message.includes(secrets.pg_migrate));
    assert.ok(!existsSync(join(d, "home", "started")));
  });

  test("leftovers of a launcher that was killed are ended by the next start", async () => {
    const b = fakeBundle(), d = join(root, "data six");
    const a = launch(b, d, "start");
    await a.until("ready");
    const st = await cli(b, d, "status");
    const pids = Object.values(st.out.services as Record<string, { pid: number }>).map((s) => s.pid);
    a.p.kill("SIGKILL"); await a.exited;
    assert.ok(pids.some(alive));                    // orphaned
    const w = launch(b, d, "start");
    await w.until("ready");
    assert.ok(!pids.some(alive));                   // reaped
    await cli(b, d, "stop"); await w.exited;
  });
});
