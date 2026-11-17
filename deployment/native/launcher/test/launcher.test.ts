// The launcher as a process, against a fake bundle (shell stand-ins for PostgreSQL and Python, a stub API): ordering, the
// ready/stop/status/health contract, a service dying, a failed step, a crashed launcher's leftovers, and no process left after a stop.
import assert from "node:assert/strict";
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { appendFileSync, chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { relative } from "node:path";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, test } from "node:test";
import { alive as aliveFn, lockPath, takeLock } from "../src/launcher.ts";

const root = mkdtempSync(join(tmpdir(), "psh-fake-"));
const main = join(import.meta.dirname, "..", "src", "main.ts"), fakeApi = join(import.meta.dirname, "fake-api.mjs");
const sh = (path: string, body: string) => { mkdirSync(join(path, ".."), { recursive: true }); writeFileSync(path, `#!/bin/sh\n${body}\n`); chmodSync(path, 0o755); };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function fakeBundle(): string {
  const b = mkdtempSync(join(root, "bundle-"));
  // initdb -D dir: creates the data folder. pg_ctl start: a live pid in postmaster.pid, after checking the settings file; stop ends it.
  sh(join(b, "pg/bin/initdb"), `while [ $# -gt 0 ]; do [ "$1" = -D ] && d=$2; shift; done; echo x >> "${b}/initdb-calls"; echo $$ > "${b}/initdb-pid"
mkdir -p "$d"; echo 18 > "$d/PG_VERSION"; : > "$d/postgresql.conf"; [ -f "${b}/slow-initdb" ] && sleep 30; exit 0`);
  sh(join(b, "pg/bin/pg_ctl"), `while [ $# -gt 0 ]; do case "$1" in -D) d=$2;; start|stop) a=$1;; esac; shift; done
if [ "$a" = start ]; then grep -q "^listen_addresses = '127.0.0.1'" "$d/launcher.conf" || exit 9; grep -q "include 'launcher.conf'" "$d/postgresql.conf" || exit 8
  sleep 600 >/dev/null 2>&1 & echo $! > "$d/postmaster.pid"; else
  [ -f "$d/postmaster.pid" ] && kill $(head -1 "$d/postmaster.pid") 2>/dev/null; rm -f "$d/postmaster.pid"; fi`);
  // Each step and service records its arguments and environment, so the test can check who got which credential.
  sh(join(b, "py/bin/python3"), `name=$(echo "$*" | sed -n 's/.*-m \\([a-z_.]*\\).*/\\1/p'); name=\${name:-createdb}
env > "$HOME/env-$name"; echo "$name" >> "$HOME/order"
[ "$name" = createdb ] && [ -f "${b}/slow-createdb" ] && sleep 30
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
  const p = spawn(process.execPath, [main, ...args, "--bundle", b, "--data-dir", d, "--port-base", String(nextBase += 20), "--json"], { stdio: ["ignore", "pipe", "pipe"], cwd: root });
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
  const p = spawn(process.execPath, [main, ...args, "--bundle", b, "--data-dir", d, "--json"], { stdio: ["ignore", "pipe", "pipe"], cwd: root });
  let text = ""; p.stdout!.on("data", (c) => (text += c));
  p.once("exit", (code) => resolve({ code, out: text.trim() ? JSON.parse(text.trim().split("\n").pop()!) : {} }));
});
const alive = (pid: number) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const pgPid = (d: string): number | undefined => {
  try { return Number(readFileSync(join(d, "pgdata/postmaster.pid"), "utf8").split("\n")[0]); } catch { return undefined; }
};
const untilFile = async (f: string, ms = 20000) => { const end = Date.now() + ms; while (!existsSync(f) && Date.now() < end) await sleep(100); assert.ok(existsSync(f), f); };
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

describe("lock", () => {
  test("a stale lock is taken over by one start only, even when another start acts in the middle of the takeover", () => {
    const d = mkdtempSync(join(root, "lock-"));
    const dead = spawnSync("true").pid!;
    writeFileSync(lockPath(d), String(dead));
    let other: number | undefined | "not run" = "not run";
    const mine = takeLock(d, { beforeBreak: () => { other = takeLock(d); } });
    assert.equal(mine, undefined, "the first start holds the lock");
    assert.notEqual(other, "not run", "the second start ran while the first was taking over");
    assert.notEqual(other, undefined, "the second start must not hold the lock too");
    assert.equal(readFileSync(lockPath(d), "utf8").trim(), String(process.pid));
  });

  test("a lock file still being written (empty) counts as held", () => {
    const d = mkdtempSync(join(root, "lock-"));
    writeFileSync(lockPath(d), "");
    assert.notEqual(takeLock(d), undefined);
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

  test("a relative data folder and bundle work (the launcher runs steps in the data folder)", async () => {
    const b = fakeBundle(), d = join(root, "data rel");
    const l = launch(relative(root, b), relative(root, d), "start");
    await l.until("ready");
    assert.equal((await cli(b, d, "stop")).code, 0);
    assert.equal(await l.exited, 0);
  });

  test("two starts at once: one runs, the other is refused, initdb runs once", async () => {
    const b = fakeBundle(), d = join(root, "data race");
    const [x, y] = [launch(b, d, "start"), launch(b, d, "start")];
    const first = await Promise.race([x.exited.then(() => x), y.exited.then(() => y)]);
    const other = first === x ? y : x;
    assert.equal(await first.exited, 1);
    assert.equal(first.events.find((e) => e.event === "error")?.code, "already_running");
    await other.until("ready");
    assert.equal(readFileSync(join(b, "initdb-calls"), "utf8").trim().split("\n").length, 1);
    await cli(b, d, "stop"); assert.equal(await other.exited, 0);
  });

  test("killed during initdb: the next start initialises again, and the server reads the launcher's settings", async () => {
    const b = fakeBundle(), d = join(root, "data initcrash");
    writeFileSync(join(b, "slow-initdb"), "");
    const a = launch(b, d, "start");
    await untilFile(join(b, "initdb-calls"));
    a.p.kill("SIGKILL"); await a.exited;
    const orphan = Number(readFileSync(join(b, "initdb-pid"), "utf8"));
    assert.ok(alive(orphan), "the killed launcher's initdb is still running");
    rmSync(join(b, "slow-initdb"));
    const w = launch(b, d, "start");
    await w.until("ready");
    assert.ok(!alive(orphan), "the next start ended the orphaned initdb before running its own");
    assert.ok(w.events.some((e) => e.stage === "initdb"));
    assert.match(readFileSync(join(d, "pgdata/postgresql.conf"), "utf8"), /include 'launcher.conf'/);
    await cli(b, d, "stop"); await w.exited;
  });

  test("a data folder whose settings include was lost is repaired on start", async () => {
    const b = fakeBundle(), d = join(root, "data repair");
    const a = launch(b, d, "start"); await a.until("ready");
    await cli(b, d, "stop"); await a.exited;
    writeFileSync(join(d, "pgdata/postgresql.conf"), "");
    const w = launch(b, d, "start"); await w.until("ready");
    assert.match(readFileSync(join(d, "pgdata/postgresql.conf"), "utf8"), /include 'launcher.conf'/);
    await cli(b, d, "stop"); await w.exited;
  });

  test("stop during initdb: exit 0, initdb ended, no server, no state, and the next start works", async () => {
    const b = fakeBundle(), d = join(root, "data stopinit");
    writeFileSync(join(b, "slow-initdb"), "");
    const a = launch(b, d, "start");
    await untilFile(join(b, "initdb-calls"));
    assert.equal((await cli(b, d, "stop")).code, 0);
    assert.equal(await a.exited, 0);
    assert.equal(spawnSync("pgrep", ["-f", `${b}/pg/bin/initdb`]).status, 1, "initdb still running");
    assert.ok(!existsSync(join(d, "launcher.json")));
    rmSync(join(b, "slow-initdb"));
    const w = launch(b, d, "start"); await w.until("ready");
    await cli(b, d, "stop"); await w.exited;
  });

  test("stop while the schema step runs: the server that was already up is stopped too", async () => {
    const b = fakeBundle(), d = join(root, "data stopschema");
    writeFileSync(join(b, "slow-createdb"), "");
    mkdirSync(join(d, "home"), { recursive: true });
    const a = launch(b, d, "start");
    await untilFile(join(d, "home", "env-createdb"));
    const pg = pgPid(d);
    assert.ok(pg && alive(pg));
    assert.equal((await cli(b, d, "stop")).code, 0);
    assert.equal(await a.exited, 0);
    assert.ok(!alive(pg!));
    assert.ok(!existsSync(join(d, "pgdata/postmaster.pid")));
  });

  test("killed after ready, then stop: the services and the server are ended", async () => {
    const b = fakeBundle(), d = join(root, "data killstop");
    const a = launch(b, d, "start"); await a.until("ready");
    const pg = pgPid(d)!;
    a.p.kill("SIGKILL"); await a.exited;
    assert.ok(alive(pg));
    assert.equal((await cli(b, d, "stop")).code, 0);
    assert.ok(!alive(pg));
  });

  test("a service binary that cannot run: start_failed, everything stopped, exit 1", async () => {
    const b = fakeBundle(), d = join(root, "data nobin");
    rmSync(join(b, "node/bin/node"));
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    assert.equal(l.events.find((e) => e.event === "error")?.code, "start_failed");
    assert.ok(l.events.some((e) => e.event === "stopped"));
    assert.ok(!existsSync(join(d, "pgdata/postmaster.pid")));
  });

  test("two detached starts at once: one is ready, the other says already_running, and neither clobbers the other's log", async () => {
    const b = fakeBundle(), d = join(root, "data detach");
    const [x, y] = await Promise.all([cli(b, d, "start", "--detach"), cli(b, d, "start", "--detach")]);
    const outs = [x, y].map((r) => r.out);
    assert.deepEqual(outs.map((o) => o.event).sort(), ["error", "ready"]);
    assert.equal(outs.find((o) => o.event === "error").code, "already_running");
    assert.match(readFileSync(join(d, "logs/launcher.log"), "utf8"), /"stage":"initdb"/);
    assert.equal((await cli(b, d, "stop")).code, 0);
  });

  test("killed after ready, then two starts at once over its stale lock: one runs, one is refused, initdb runs once", async () => {
    const b = fakeBundle(), d = join(root, "data stalelock");
    const a = launch(b, d, "start"); await a.until("ready");
    a.p.kill("SIGKILL"); await a.exited;
    assert.ok(existsSync(join(d, "launcher.lock")), "a killed launcher leaves its lock behind");
    for (let round = 0; round < 3; round++) {
      const [x, y] = [launch(b, d, "start"), launch(b, d, "start")];
      const first = await Promise.race([x.exited.then(() => x), y.exited.then(() => y)]);
      const other = first === x ? y : x;
      assert.equal(first.events.find((e) => e.event === "error")?.code, "already_running");
      await other.until("ready");
      await cli(b, d, "stop"); assert.equal(await other.exited, 0);
    }
    assert.equal(readFileSync(join(b, "initdb-calls"), "utf8").trim().split("\n").length, 1);
  });

  test("killed during initdb, then stop: the initdb step is ended too", async () => {
    const b = fakeBundle(), d = join(root, "data killinit");
    writeFileSync(join(b, "slow-initdb"), "");
    const a = launch(b, d, "start");
    await untilFile(join(b, "initdb-calls"));
    a.p.kill("SIGKILL"); await a.exited;
    assert.equal(spawnSync("pgrep", ["-f", `${b}/pg/bin/initdb`]).status, 0, "the orphaned initdb is still running");
    assert.equal((await cli(b, d, "stop")).code, 0);
    await sleep(500);
    assert.equal(spawnSync("pgrep", ["-f", `${b}/pg/bin/initdb`]).status, 1, "initdb still running after stop");
  });
});
