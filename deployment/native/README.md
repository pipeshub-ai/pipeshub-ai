# Native install (no Docker)

PipesHub on one computer without containers: the bundled PostgreSQL (pgvector), the Python services, the placement and fold
workers and the Node API, started by a launcher. Every store runs on PostgreSQL, with the settings of
`deployment/docker-compose/docker-compose.postgres-only.yml`, and every process listens on 127.0.0.1 on ports the launcher picks.

```
<bundle>/launcher/       this launcher (run by the bundle's Node: `node launcher/src/main.ts <command>`)
<bundle>/node/bin/node   Node 24.12 or later
<bundle>/py/bin/python3  Python 3.12 with the backend's dependencies
<bundle>/pg/bin/         PostgreSQL 18 (initdb, pg_ctl, postgres) with pgvector and pg_textsearch
<bundle>/app/backend/    the API as in the image: dist/index.js, dist/public (web UI), dist/node_modules, src/ (templates, OpenAPI spec)
<bundle>/app/python/     the Python services' source (app/)
<bundle>/app/db/         the schema tools (db.bootstrap_roles, db.migrate, db.maintain)
<data>/                  created on first run, mode 0700: pgdata/ logs/ home/ run/ secrets.json ports.json launcher.json
```

Data defaults to `~/Library/Application Support/PipesHub` (macOS) or `${XDG_DATA_HOME:-~/.local/share}/pipeshub`; `--data-dir` or
`PIPESHUB_HOME` overrides it. Nothing is written into the bundle.

## What `start` does

0. Takes the data folder's lock: an `flock` on `launcher.lock`, held by a small helper process (the bundle's Python) for the
   launcher's lifetime, so the kernel releases it when the launcher dies. A second start on the same folder reports
   `already_running`. Anything a crashed launcher left (its current step, services, PostgreSQL) is ended first.
1. First run: `initdb` (SCRAM authentication, the administrator password from `secrets.json`) into a side folder, renamed into
   place when complete. A `pgdata` that holds files but no `PG_VERSION` is never initialised over: the start stops and says so.
2. Writes `<pgdata>/launcher.conf` (loopback, the picked port, a 0700 socket folder where the path fits, the Compose image's
   `shared_preload_libraries`) and starts PostgreSQL.
3. Creates the `pipeshub` database if missing and gives every login that exists its password from `secrets.json`, then
   `python -m db.bootstrap_roles` (the administrator login; creates the missing logins) and `python -m db.migrate` (the migrate login). Every start, so an upgrade migrates. If a step fails, nothing else starts.
4. Starts query, connectors, indexing, embedding and docling (`python -m app.<service>_main`), each as its own database login, as
   `process_monitor.sh` does; `db.maintain placement --watch` and `fold --watch`; then the API (`node dist/index.js`).
5. Waits for `/api/v1/health`.

`secrets.json` (0600) holds the generated passwords and signing secrets. They are passed to the processes in their environment, never
on a command line, and masked in any error message the launcher prints. A `secrets.json` that cannot be read or parsed is never
replaced: the start stops and says so, because the database already uses those passwords. The same holds for one without
`pg_admin` once the database exists.

## Launcher contract (what the desktop app uses)

| Command | Behaviour | Exit |
|---|---|---|
| `start [--data-dir D] [--json] [--detach]` | the steps above; stays in the foreground until TERM/INT or a service dies; `--detach` returns after `ready` | 0 stopped on request, 1 start-up failed, 3 a service died |
| `stop [--data-dir D] [--json]` | stops a running launcher and everything under it; also ends leftovers of a crashed launcher | 0 stopped (also when none runs), 1 `stop_failed` |
| `status [--data-dir D] [--json]` | `{"running","phase","pid","url","ports","services":{name:{pid,alive}}}` | 0 running, 3 not |
| `health [--data-dir D] [--json]` | `{"healthy","checks":{api,pg,...}}` | 0 healthy, 1 not |

`--json` prints one object per line, each with an `event` field: `progress` (`stage`: initdb, postgres, schema, services, health;
`message`: the same for people), `ready` (`url`, `ports`, `healthy_after_s`, `pid`, `data_dir`), `service_exited` (`service`, `code`,
`signal`), `stopping`, `stopped`, `error` (`code`: already_running, port_in_use, start_failed, not_healthy, stop_failed, not_ready;
`message`). Other options: `--bundle D` (default: the folder above `launcher/`), `--port-base N` (scan upward from N). The
services' ports are kept from run to run while free. The web port never moves once the database exists (the API's address is
stored in its settings), so if another program has taken it the start fails with `port_in_use`.

## Tests

`cd deployment/native/launcher && npm test` (Linux or macOS, Node 24.12 or later, `python3` and `pgrep` on `PATH`; no npm
dependencies). The launcher tests run it as a process against
a fake bundle (shell stand-ins for PostgreSQL and Python, a stub API).
