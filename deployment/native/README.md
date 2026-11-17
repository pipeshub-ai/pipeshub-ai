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

1. First run: `initdb` (SCRAM authentication, the administrator password from `secrets.json`).
2. Writes `<pgdata>/launcher.conf` (loopback, the picked port, a 0700 socket folder where the path fits, the Compose image's
   `shared_preload_libraries`) and starts PostgreSQL.
3. Creates the `pipeshub` database if missing, then `python -m db.bootstrap_roles` (the administrator login; every login's password)
   and `python -m db.migrate` (the migrate login). Every start, so an upgrade migrates. If a step fails, nothing else starts.
4. Starts query, connectors, indexing, embedding and docling (`python -m app.<service>_main`), each as its own database login, as
   `process_monitor.sh` does; `db.maintain placement --watch` and `fold --watch`; then the API (`node dist/index.js`).
5. Waits for `/api/v1/health`.

`secrets.json` (0600) holds the generated passwords and signing secrets. They are passed to the processes in their environment, never
on a command line, and masked in any error message the launcher prints.

## Launcher contract (what the desktop app uses)

| Command | Behaviour | Exit |
|---|---|---|
| `start [--data-dir D] [--json] [--detach]` | the steps above; stays in the foreground until TERM/INT or a service dies; `--detach` returns after `ready` | 0 stopped on request, 1 start-up failed, 3 a service died |
| `stop [--data-dir D] [--json]` | stops a running launcher and everything under it; also ends leftovers of a crashed launcher | 0 (also when none runs) |
| `status [--data-dir D] [--json]` | `{"running","phase","pid","url","ports","services":{name:{pid,alive}}}` | 0 running, 3 not |
| `health [--data-dir D] [--json]` | `{"healthy","checks":{api,pg,...}}` | 0 healthy, 1 not |

`--json` prints one object per line, each with an `event` field: `progress` (`stage`: initdb, postgres, schema, services, health;
`message`: the same for people), `ready` (`url`, `ports`, `healthy_after_s`, `pid`, `data_dir`), `service_exited` (`service`, `code`,
`signal`), `stopping`, `stopped`, `error` (`code`: already_running, start_failed, not_healthy, stop_failed, not_ready; `message`).
Other options: `--bundle D` (default: the folder above `launcher/`), `--port-base N` (scan upward from N). A port used by the previous
run is kept while it is free, so the web address stays the same.

## Tests

`cd deployment/native/launcher && npm test` (Node 24.12 or later; no dependencies). The launcher tests run it as a process against
a fake bundle (shell stand-ins for PostgreSQL and Python, a stub API).
