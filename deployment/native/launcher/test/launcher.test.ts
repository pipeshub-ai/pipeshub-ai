// The launcher as a process, against a fake bundle (shell stand-ins for PostgreSQL and Python, a stub API): ordering, the
// ready/stop/status/health contract, a service dying, a failed step, a crashed launcher's leftovers, and no process left after a stop.
import assert from "node:assert/strict";
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { appendFileSync, chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { relative } from "node:path";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, test } from "node:test";
import { layout, loadSecrets } from "../src/config.ts";
import { alive as aliveFn, health, procIdentity, startDetached, startPostgres, stopPostgres } from "../src/launcher.ts";

const root = mkdtempSync(join(tmpdir(), "psh-fake-"));
const main = join(import.meta.dirname, "..", "src", "main.ts"), fakeApi = join(import.meta.dirname, "fake-api.mjs");
const sh = (path: string, body: string) => { mkdirSync(join(path, ".."), { recursive: true }); writeFileSync(path, `#!/bin/sh\n${body}\n`); chmodSync(path, 0o755); };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function fakeBundle(): string {
  const b = mkdtempSync(join(root, "bundle-"));
  // initdb -D dir: creates the data folder. pg_ctl start: a live pid in postmaster.pid (working in the data folder, as a postmaster
  // does), after checking the settings file; stop ends it. With slow-pgstart, the server comes up 2 s later in a session of its own, as
  // pg_ctl's child does, and pg_ctl waits for it.
  sh(join(b, "pg/bin/initdb"), `while [ $# -gt 0 ]; do [ "$1" = -D ] && d=$2; shift; done; echo x >> "${b}/initdb-calls"; echo $$ > "${b}/initdb-pid"
mkdir -p "$d"; echo 18 > "$d/PG_VERSION"; : > "$d/postgresql.conf"; [ -f "${b}/slow-initdb" ] && sleep 30; exit 0`);
  sh(join(b, "pg/bin/pg_ctl"), `while [ $# -gt 0 ]; do case "$1" in -D) d=$2;; start|stop) a=$1;; esac; shift; done
if [ "$a" = start ]; then grep -q "^listen_addresses = '127.0.0.1'" "$d/launcher.conf" || exit 9; grep -q "include 'launcher.conf'" "$d/postgresql.conf" || exit 8
  cd "$d"; touch "${b}/pgstart-called"
  if [ -f "${b}/slow-pgstart" ]; then
    python3 -c 'import os, sys, time; os.setsid(); time.sleep(2); open("postmaster.pid", "w").write("%d\\n%s\\n" % (os.getpid(), sys.argv[1])); os.execvp("sleep", ["sleep", "600"])' "$d" >/dev/null 2>&1 &
    while [ ! -f "$d/postmaster.pid" ]; do sleep 0.1; done
  else sleep 600 >/dev/null 2>&1 & printf '%s\\n%s\\n' $! "$d" > "$d/postmaster.pid"; fi; else
  [ -f "${b}/slow-pgstop" ] && sleep 40
  [ -f "$d/postmaster.pid" ] && kill $(head -1 "$d/postmaster.pid") 2>/dev/null; rm -f "$d/postmaster.pid"; fi`);
  // Each step and service records its arguments and environment, so the test can check who got which credential.
  sh(join(b, "py/bin/python3"), `case "$*" in *fcntl*) exec python3 "$@";; esac
name=$(echo "$*" | sed -n 's/.*-m \\([a-z_.]*\\).*/\\1/p'); name=\${name:-createdb}
env > "$HOME/env-$name"; echo "$name" >> "$HOME/order"
[ "$name" = createdb ] && [ -f "${b}/slow-createdb" ] && sleep 30
while [ "$name" = createdb ] && [ -f "${b}/hold-createdb" ]; do sleep 0.1; done
case "$*" in
  *app.*_main*) [ -f "$HOME/die-$name" ] && { sleep 1; exit 7; }; [ -f "$HOME/fork-$name" ] && { sleep 600 & wait; exit 0; }; exec sleep 600;;
  *db.maintain*) exec sleep 600;;
  *db.bootstrap_roles*) [ -n "$PG_BACKUP_PASSWORD" ] || exit 2;;
esac
exit 0`);
  sh(join(b, "node/bin/node"), `exec "${process.execPath}" "${fakeApi}"`);
  for (const d of ["app/python", "app/backend"]) mkdirSync(join(b, d), { recursive: true });
  return b;
}

const live: ChildProcess[] = [];
/** Every bundle and data folder a test started, so teardown stops each deployment (detached ones too) before deleting it. */
const deployments = new Map<string, string>();
let nextBase = 54800;
function launch(b: string, d: string, ...args: string[]) {
  deployments.set(d, b);
  const p = spawn(process.execPath, [main, ...args, "--bundle", b, "--data-dir", d, "--port-base", String(nextBase += 20), "--json"], { stdio: ["ignore", "pipe", "pipe"], cwd: root });
  live.push(p);
  const events: Record<string, any>[] = [];
  let buf = "";
  p.stdout!.on("data", (chunk) => { buf += chunk; let i; while ((i = buf.indexOf("\n")) >= 0) { events.push(JSON.parse(buf.slice(0, i))); buf = buf.slice(i + 1); } });
  // close, not exit: by then stdout is drained and every event is in
  const exited = new Promise<number>((r) => p.once("close", (c) => r(c ?? -1)));
  const until = async (event: string, ms = 30000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const e = events.find((x) => x.event === event); if (e) return e; await sleep(100); }
    throw new Error(`no '${event}' event; got ${JSON.stringify(events)}`);
  };
  return { p, events, exited, until };
}
// async on purpose: a blocking spawnSync would stop this process reaping the launcher it started, leaving a zombie that still "exists"
const cli = (b: string, d: string, ...args: string[]) => new Promise<{ code: number | null; out: any }>((resolve) => {
  deployments.set(d, b);
  const p = spawn(process.execPath, [main, ...args, "--bundle", b, "--data-dir", d, "--json"], { stdio: ["ignore", "pipe", "pipe"], cwd: root });
  let text = ""; p.stdout!.on("data", (c) => (text += c));
  p.once("close", (code) => resolve({ code, out: text.trim() ? JSON.parse(text.trim().split("\n").pop()!) : {} }));
});
const alive = (pid: number) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const pgPid = (d: string): number | undefined => {
  try { return Number(readFileSync(join(d, "pgdata/postmaster.pid"), "utf8").split("\n")[0]); } catch { return undefined; }
};
const untilFile = async (f: string, ms = 20000) => { const end = Date.now() + ms; while (!existsSync(f) && Date.now() < end) await sleep(100); assert.ok(existsSync(f), f); };
const envOf = (d: string, name: string) => Object.fromEntries(readFileSync(join(d, "home", `env-${name}`), "utf8").trim().split("\n").map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));

after(() => {
  for (const [d, b] of deployments) spawnSync(process.execPath, [main, "stop", "--bundle", b, "--data-dir", d], { cwd: root, timeout: 60_000, stdio: "ignore" });
  for (const p of live) try { p.kill("SIGKILL"); } catch { /* gone */ }
  rmSync(root, { recursive: true, force: true });
});

describe("alive", () => {
  test("a child that exited but was not reaped is not alive", () => {
    const c = spawn("true");
    spawnSync("sleep", ["0.5"]);                      // blocks this process's loop, so the exited child stays a zombie
    assert.equal(aliveFn(c.pid), false);
    assert.equal(aliveFn(process.pid), true);
    assert.equal(aliveFn(undefined), false);
  });
});

describe("PostgreSQL ownership", () => {
  test("a server that does not stop is reported, not taken as stopped", async () => {
    const b = mkdtempSync(join(root, "bundle-")), d = mkdtempSync(join(root, "data-")), lay = layout(b, d);
    sh(join(b, "pg/bin/pg_ctl"), "exit 1");                       // pg_ctl cannot stop it either
    mkdirSync(lay.pgData);
    const pm = spawn("sh", ["-c", "trap '' INT; exec sleep 60"], { cwd: lay.pgData, stdio: "ignore" }); live.push(pm);
    writeFileSync(join(lay.pgData, "postmaster.pid"), `${pm.pid}\n${lay.pgData}\n`);
    await sleep(200);
    await assert.rejects(stopPostgres(lay, 300), /did not stop/);
    pm.kill("SIGKILL");
  });
  test("a pg_ctl start whose pid cannot be recorded is still waited for, so its server is found", async () => {
    const b = fakeBundle(), d = mkdtempSync(join(root, "data-")), lay = layout(b, d);
    writeFileSync(join(b, "slow-pgstart"), "");
    for (const f of [lay.pgData, lay.logs]) mkdirSync(f, { recursive: true });
    writeFileSync(join(lay.pgData, "PG_VERSION"), "18\n"); writeFileSync(join(lay.pgData, "postgresql.conf"), "include 'launcher.conf'\n");
    const c = { layout: lay, ports: { api: 1, pg: 2, query: 3, connectors: 4, indexing: 5, embedding: 6, docling: 7 }, secrets: loadSecrets(d) };
    const steps = { cancelled: false, onSpawn: (name: string, pid: number | undefined) => { if (name === "pg_ctl" && pid) throw new Error("disk full"); } };
    await assert.rejects(startPostgres(c, () => {}, steps), /disk full/);
    const pg = pgPid(d), up = !!pg && alive(pg);
    if (up) process.kill(pg!, "SIGKILL");
    else await sleep(3000);   // the server pg_ctl was still handing over to: ended once it appears
    const late = pgPid(d); if (!up && late && alive(late)) process.kill(late, "SIGKILL");
    assert.ok(up, "the start failed before its server was up, so a shutdown would not find it");
  });
});

describe("health", () => {
  test("a launcher that leaves ready while the probe is pending is not reported healthy", async () => {
    const d = mkdtempSync(join(root, "data-")), pgData = join(d, "pgdata");
    mkdirSync(pgData);
    const pm = spawn("sleep", ["30"], { cwd: pgData, stdio: "ignore" }); live.push(pm);
    writeFileSync(join(pgData, "postmaster.pid"), `${pm.pid}\n${pgData}\n`);
    const state = (phase: string) => writeFileSync(join(d, "launcher.json"), JSON.stringify({ pid: process.pid, pidId: procIdentity(process.pid), phase, services: {}, ports: { api: 1 }, url: "" }));
    state("ready");
    const ok = async () => new Response(null, { status: 200 });
    assert.equal((await health({ dataDir: d }, ok)).body.healthy, true);
    assert.equal((await health({ dataDir: d }, async () => { state("stopping"); return ok(); })).body.healthy, false);
    pm.kill();
  });
});

describe("start --detach", () => {
  test("out of time: the background start is stopped before not_ready is reported", async () => {
    const d = mkdtempSync(join(root, "data-")), marker = join(d, "got-term"), pidFile = join(d, "child-pid");
    const child = `require("fs").writeFileSync(${JSON.stringify(pidFile)}, String(process.pid));
process.on("SIGTERM", () => { require("fs").writeFileSync(${JSON.stringify(marker)}, ""); process.exit(0); }); setInterval(() => {}, 1000);`;
    const events: Record<string, unknown>[] = [];
    // healthTimeoutS + 120 s is the wait: 2 s here
    const code = await startDetached({ dataDir: d, bundle: root, healthTimeoutS: -118 }, (e) => events.push(e), [], [process.execPath, "-e", child]);
    assert.equal(code, 1);
    assert.equal(events.at(-1)?.code, "not_ready");
    const pid = Number(readFileSync(pidFile, "utf8"));
    if (!existsSync(marker)) { try { process.kill(pid, "SIGKILL"); } catch { /* gone */ } }
    assert.ok(existsSync(marker), "the background start was left running");
    assert.ok(!alive(pid));
  });
});

describe("start --detach, cannot run", () => {
  test("a background launcher that cannot be started is reported, not thrown", async () => {
    const d = mkdtempSync(join(root, "data-")), events: Record<string, unknown>[] = [];
    assert.equal(await startDetached({ dataDir: d, bundle: root }, (e) => events.push(e), [], [join(d, "no-such-node")]), 1);
    assert.equal(events.at(-1)?.code, "start_failed");
    assert.match(String(events.at(-1)?.message), /ENOENT/);
  });
  test("the background launcher's own error is read from its bytes, after an earlier non-ASCII line", async () => {
    const d = mkdtempSync(join(root, "data-")), events: Record<string, unknown>[] = [];
    mkdirSync(join(d, "logs"));
    writeFileSync(join(d, "logs", "launcher.log"), "/home/José\n");
    const child = `console.log(JSON.stringify({ event: "error", code: "port_in_use", message: "taken" }))`;
    assert.equal(await startDetached({ dataDir: d, bundle: root }, (e) => events.push(e), [], [process.execPath, "-e", child]), 1);
    assert.equal(events.at(-1)?.code, "port_in_use");
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
    sh(join(b, "py/bin/python3"), 'case "$*" in *fcntl*) exec python3 "$@";; esac; echo "boom: $PGPASSWORD"; exit 1');
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    const err = l.events.find((e) => e.event === "error");
    assert.equal(err?.code, "start_failed");
    assert.match(err?.message, /createdb failed/);
    assert.match(err?.message, /boom: \*\*\*/);
    const secrets = JSON.parse(readFileSync(join(d, "secrets.json"), "utf8"));
    assert.ok(!JSON.stringify(l.events).includes(secrets.pg_admin));
    assert.ok(!existsSync(join(d, "pgdata/postmaster.pid")));
  });

  test("a failing migration stops the start before any service runs", async () => {
    const b = fakeBundle(), d = join(root, "data five");
    sh(join(b, "py/bin/python3"), `case "$*" in *fcntl*) exec python3 "$@";; *db.migrate*) echo "migration failed: password=$PG_MIGRATE_URL"; exit 1;; *app.*|*db.maintain*) touch "$HOME/started"; exec sleep 600;; esac; exit 0`);
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

  test("a malformed secrets file is never replaced: the start fails and says why", async () => {
    const b = fakeBundle(), d = join(root, "data badsecrets");
    mkdirSync(d, { recursive: true });
    writeFileSync(join(d, "secrets.json"), "{not json", { mode: 0o600 });
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    assert.match(l.events.find((e) => e.event === "error")?.message ?? "", /secrets\.json/);
    assert.equal(readFileSync(join(d, "secrets.json"), "utf8"), "{not json");
    assert.ok(!existsSync(join(b, "initdb-calls")));
  });

  test("the web port is kept for the life of the data folder: when it is taken, the start refuses instead of moving", async () => {
    const b = fakeBundle(), d = join(root, "data sticky");
    const a = launch(b, d, "start"); const r = await a.until("ready");
    await cli(b, d, "stop"); await a.exited;
    const squat = spawn(process.execPath, ["-e", `require("http").createServer().listen(${r.ports.api}, "127.0.0.1")`], { stdio: "ignore" });
    live.push(squat);
    await sleep(500);
    const w = launch(b, d, "start");
    assert.equal(await w.exited, 1);
    const err = w.events.find((e) => e.event === "error");
    assert.equal(err?.code, "port_in_use");
    assert.match(err?.message ?? "", new RegExp(String(r.ports.api)));
    squat.kill();
  });

  test("an unreadable lock file stops the start rather than being taken over", async () => {
    const b = fakeBundle(), d = join(root, "data badlock");
    mkdirSync(d, { recursive: true });
    mkdirSync(join(d, "launcher.lock"));   // unopenable for writing even by root, unlike a mode-000 file
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    assert.match(l.events.find((e) => e.event === "error")?.message ?? "", /launcher\.lock/);
    assert.ok(!existsSync(join(b, "initdb-calls")));
  });

  test("a state file naming someone else's live pid: not reported as running, never signalled, and the start proceeds", async () => {
    const b = fakeBundle(), d = join(root, "data pidreuse");
    const stranger = spawn("sleep", ["60"], { stdio: "ignore" }); live.push(stranger);
    mkdirSync(d, { recursive: true });
    writeFileSync(join(d, "launcher.json"), JSON.stringify({ pid: stranger.pid, pidId: "a-process-that-ended-long-ago", phase: "ready", services: {}, ports: {} }));
    assert.equal((await cli(b, d, "status")).code, 3);
    assert.equal((await cli(b, d, "stop")).code, 0);
    assert.ok(alive(stranger.pid!), "stop signalled an unrelated process");
    const w = launch(b, d, "start"); await w.until("ready");
    assert.ok(alive(stranger.pid!));
    await cli(b, d, "stop"); await w.exited;
    stranger.kill();
  });

  test("a service whose leader died leaves a worker: after a killed launcher, stop still ends the worker", async () => {
    const b = fakeBundle(), d = join(root, "data group");
    mkdirSync(join(d, "home"), { recursive: true }); writeFileSync(join(d, "home", "fork-app.indexing_main"), "");
    const a = launch(b, d, "start"); await a.until("ready");
    const leader = JSON.parse(readFileSync(join(d, "launcher.json"), "utf8")).services.indexing as number;
    a.p.kill("SIGKILL"); await a.exited;
    process.kill(leader, "SIGKILL");
    await sleep(300);
    let groupAlive = true; try { process.kill(-leader, 0); } catch { groupAlive = false; }
    assert.ok(groupAlive, "the worker should outlive its leader");
    assert.equal((await cli(b, d, "stop")).code, 0);
    await sleep(300);
    assert.throws(() => process.kill(-leader, 0), "the worker survived stop");
  });

  test("stop and start at once after a crash: a start that comes up keeps its server and its state", async () => {
    const b = fakeBundle(), d = join(root, "data stopstart");
    writeFileSync(join(b, "slow-initdb"), "");
    const a = launch(b, d, "start");
    await untilFile(join(b, "initdb-calls"));
    a.p.kill("SIGKILL"); await a.exited;
    rmSync(join(b, "slow-initdb"));
    const stopping = cli(b, d, "stop");
    const w = launch(b, d, "start");
    await stopping;
    const outcome = await Promise.race([w.until("ready", 60000).then(() => "ready"), w.exited.then(() => "exited")]);
    if (outcome === "ready" && !w.events.some((e) => e.event === "stopping")) {
      await sleep(1000);
      assert.ok(existsSync(join(d, "launcher.json")), "the new start's state was removed");
      assert.ok(pgPid(d) && alive(pgPid(d)!), "the new start's server was stopped");
    }
    await cli(b, d, "stop"); await w.exited;
  });

  test("a data folder with files but no PG_VERSION is never initialised over", async () => {
    const b = fakeBundle(), d = join(root, "data noversion");
    mkdirSync(join(d, "pgdata", "base"), { recursive: true });
    writeFileSync(join(d, "pgdata", "base", "1"), "rows");
    loadSecrets(d);
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    assert.match(l.events.find((e) => e.event === "error")?.message ?? "", /PG_VERSION/);
    assert.equal(readFileSync(join(d, "pgdata", "base", "1"), "utf8"), "rows");
    assert.ok(!existsSync(join(b, "initdb-calls")));
  });

  test("stop while pg_ctl start hands over to the server: no server comes up after the stop", async () => {
    const b = fakeBundle(), d = join(root, "data pghandover");
    writeFileSync(join(b, "slow-pgstart"), "");
    const a = launch(b, d, "start");
    await untilFile(join(b, "pgstart-called"));
    assert.equal((await cli(b, d, "stop")).code, 0);
    assert.equal(await a.exited, 0);
    await sleep(3000);
    const pg = pgPid(d), up = !!pg && alive(pg);
    if (up) process.kill(pg!, "SIGKILL");
    assert.ok(!up, "a server came up after the stop");
  });

  test("killed while pg_ctl start hands over, then stop: no server comes up after the stop", async () => {
    const b = fakeBundle(), d = join(root, "data pghandover2");
    writeFileSync(join(b, "slow-pgstart"), "");
    const a = launch(b, d, "start");
    await untilFile(join(b, "pgstart-called"));
    a.p.kill("SIGKILL"); await a.exited;
    assert.equal((await cli(b, d, "stop")).code, 0);
    await sleep(3000);
    const pg = pgPid(d), up = !!pg && alive(pg);
    if (up) process.kill(pg!, "SIGKILL");
    assert.ok(!up, "a server came up after the stop");
  });

  test("a postmaster.pid naming another live process: that process is never signalled", async () => {
    const b = fakeBundle(), d = join(root, "data pmreuse");
    const stranger = spawn("sleep", ["60"], { stdio: "ignore" }); live.push(stranger);
    mkdirSync(join(d, "pgdata"), { recursive: true });
    writeFileSync(join(d, "pgdata", "postmaster.pid"), `${stranger.pid}\n${join(d, "pgdata")}\n`);
    assert.equal((await cli(b, d, "stop")).code, 0);
    assert.ok(alive(stranger.pid!), "stop signalled a process that is not the server");
    stranger.kill();
  });

  test("a launcher that crashed while ending another's leftovers: stop ends both", async () => {
    const b = fakeBundle(), d = join(root, "data chain");
    const dead = spawnSync("true").pid;
    const worker = spawn("sleep", ["60"], { detached: true, stdio: "ignore" }); live.push(worker);
    mkdirSync(d, { recursive: true });
    writeFileSync(join(d, "launcher.json"), JSON.stringify({ pid: dead, phase: "starting", services: {}, ports: {},
      leftover: { pid: dead, phase: "ready", services: { query: worker.pid }, ports: {} } }));
    assert.equal((await cli(b, d, "stop")).code, 0);
    await sleep(300);
    const up = alive(worker.pid!);
    if (up) worker.kill("SIGKILL");
    assert.ok(!up, "the first launcher's service survived");
  });

  test("a suspended start-up step does not hold the stop: it is ended after a grace period", async () => {
    const b = fakeBundle(), d = join(root, "data stuckstep");
    sh(join(b, "py/bin/python3"), `case "$*" in *fcntl*) exec python3 "$@";; *db.migrate*) echo $$ > "$HOME/migrate-pid"; kill -STOP $$;; esac; exit 0`);
    mkdirSync(join(d, "home"), { recursive: true });
    const a = launch(b, d, "start");
    await untilFile(join(d, "home", "migrate-pid"));
    const step = Number(readFileSync(join(d, "home", "migrate-pid"), "utf8"));
    const r = await Promise.race([cli(b, d, "stop"), sleep(40_000).then(() => null)]);
    if (!r) { process.kill(step, "SIGKILL"); a.p.kill("SIGKILL"); }
    assert.equal(r?.code, 0, "stop waited on the suspended step");
    assert.equal(await a.exited, 0);
    assert.ok(!alive(step));
  });

  test("a status write that fails when a step ends: the start fails and still stops what it started", async () => {
    const b = fakeBundle(), d = join(root, "data fullstate");
    writeFileSync(join(b, "hold-createdb"), "");
    mkdirSync(join(d, "home"), { recursive: true });
    const a = launch(b, d, "start");
    await untilFile(join(d, "home", "env-createdb"));
    const pg = pgPid(d)!;
    mkdirSync(join(d, `launcher.json.${a.p.pid}.tmp`));   // the next status write fails, as on a full disk
    rmSync(join(b, "hold-createdb"));
    assert.equal(await a.exited, 1);
    assert.equal(a.events.find((e) => e.event === "error")?.code, "start_failed");
    assert.ok(a.events.some((e) => e.event === "stopped"));
    const up = alive(pg);
    if (up) process.kill(pg, "SIGKILL");
    assert.ok(!up, "the server was left running");
  });

  test("stop with a data folder that was never created: nothing runs, exit 0", async () => {
    assert.deepEqual(await cli(fakeBundle(), join(root, "data never"), "stop"), { code: 0, out: { event: "stopped", was_running: false } });
  });

  test("a state file that cannot be written: start reports start_failed and frees the lock", async () => {
    const b = fakeBundle(), d = join(root, "data nostate");
    mkdirSync(join(d, "launcher.json", "x"), { recursive: true });   // rename onto a non-empty folder fails
    const l = launch(b, d, "start");
    assert.equal(await l.exited, 1);
    assert.equal(l.events.find((e) => e.event === "error")?.code, "start_failed");
    rmSync(join(d, "launcher.json"), { recursive: true });
    const w = launch(b, d, "start"); await w.until("ready");   // the lock was released
    await cli(b, d, "stop"); await w.exited;
  });

  test("a ports.json that is not a map: a new folder gets new ports; an existing database is not given a new web port", async () => {
    const b = fakeBundle(), d = join(root, "data portsnull");
    mkdirSync(d, { recursive: true });
    writeFileSync(join(d, "ports.json"), "null");
    const a = launch(b, d, "start"); await a.until("ready");
    await cli(b, d, "stop"); await a.exited;
    rmSync(join(d, "ports.json"));
    const w = launch(b, d, "start");
    assert.equal(await w.exited, 1);
    assert.match(w.events.find((e) => e.event === "error")?.message ?? "", /ports\.json has no web port/);
  });

  test("leftovers this start could not end stay on record after it stops", async () => {
    const b = fakeBundle(), d = join(root, "data keepleftover");
    // a group whose only member is a zombie nobody reaps: it answers kill(-pgid, 0) after SIGKILL, so it cannot be proven gone
    // SIGTERM makes the holder reap its zombie itself, so nothing is left for whoever adopts it
    const holder = spawn("python3", ["-c", "import os, signal, sys, time\npid = os.fork()\nif pid == 0:\n    os.setpgid(0, 0); os._exit(0)\nsignal.signal(signal.SIGTERM, lambda *a: (os.waitpid(pid, 0), sys.exit(0)))\nprint(pid, flush=True); time.sleep(60)"], { stdio: ["ignore", "pipe", "ignore"] });
    live.push(holder);
    const closed = new Promise((r) => holder.once("close", r));
    try {
      const zpid = Number(await new Promise<string>((r) => holder.stdout!.once("data", (c) => r(String(c)))));
      mkdirSync(d, { recursive: true });
      writeFileSync(join(d, "launcher.json"), JSON.stringify({ pid: spawnSync("true").pid, phase: "ready", services: { query: zpid }, ports: {} }));
      // twice: a retry that fails the same way keeps the record without wrapping it again
      for (let round = 0; round < 2; round++) {
        const l = launch(b, d, "start");
        assert.equal(await l.exited, 1);
        assert.match(l.events.find((e) => e.event === "error")?.message ?? "", /still running after SIGKILL/);
        const kept = existsSync(join(d, "launcher.json")) ? JSON.parse(readFileSync(join(d, "launcher.json"), "utf8")) : undefined;
        assert.equal(kept?.leftover?.services?.query, zpid, "the record of the group it could not end was deleted");
        assert.equal(kept?.leftover?.leftover, undefined, `round ${round}: the record was wrapped again`);
      }
    } finally { holder.kill("SIGTERM"); await closed; }
  });

  test("an existing database whose saved web port is not a port: refused, not given a new one", async () => {
    const b = fakeBundle(), d = join(root, "data apizero");
    const a = launch(b, d, "start"); await a.until("ready");
    await cli(b, d, "stop"); await a.exited;
    const ports = JSON.parse(readFileSync(join(d, "ports.json"), "utf8"));
    writeFileSync(join(d, "ports.json"), JSON.stringify({ ...ports, api: 0 }));
    const w = launch(b, d, "start");
    assert.equal(await w.exited, 1);
    assert.match(w.events.find((e) => e.event === "error")?.message ?? "", /ports\.json has no web port/);
    assert.equal(JSON.parse(readFileSync(join(d, "ports.json"), "utf8")).api, 0, "ports.json was rewritten");
  });

  test("a dead launcher whose lock is still held a moment: stop waits for it and ends the leftovers", async () => {
    const b = fakeBundle(), d = join(root, "data lingerlock");
    mkdirSync(d, { recursive: true });
    const launcher = spawn("sleep", ["60"], { stdio: "ignore" }); live.push(launcher);
    const worker = spawn("sleep", ["60"], { detached: true, stdio: "ignore" }); live.push(worker);
    await sleep(100);
    writeFileSync(join(d, "launcher.json"), JSON.stringify({ pid: launcher.pid, pidId: procIdentity(launcher.pid!), phase: "ready", services: { query: worker.pid }, ports: {} }));
    // holds the flock for 2 s, as a dead launcher's helper does until it sees its pipe close
    const holder = spawn("python3", ["-c", "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600); fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(2)", join(d, "launcher.lock")], { stdio: ["ignore", "pipe", "ignore"] });
    live.push(holder);
    await new Promise((r) => holder.stdout!.once("data", r));
    const r = await cli(b, d, "stop");
    await sleep(300);
    const up = alive(worker.pid!);
    if (up) worker.kill("SIGKILL");
    assert.equal(r.code, 0, JSON.stringify(r.out));
    assert.ok(!up, "stop reported stopped without ending the dead launcher's service");
  });

  test("stop during a start's long cleanup of a crashed launcher's leftovers stops that start", async () => {
    const b = fakeBundle(), d = join(root, "data longreap");
    const a = launch(b, d, "start"); await a.until("ready");
    a.p.kill("SIGKILL"); await a.exited;          // its PostgreSQL stays up
    writeFileSync(join(b, "slow-pgstop"), "");     // stopping it now takes 40 s, longer than stop's 30 s wait for a start
    const w = launch(b, d, "start");
    await sleep(1500);
    const r = await cli(b, d, "stop");
    assert.equal(r.code, 0, JSON.stringify(r.out));
    assert.equal(await w.exited, 0);
    assert.ok(!w.events.some((e) => e.event === "ready"), "the start went on to ready after stop");
  });
});
