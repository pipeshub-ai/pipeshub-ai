#!/usr/bin/env bash
# The move scripts against real containers (MongoDB, PostgreSQL, PgBouncer, the schema job and a stand-in application): a dry run
# (the import login is closed before and after, open and usable by the copy while the migrator runs, its password gone afterwards, a
# later schema run's surface check passes), a failing move, then `--flip` and `rollback-to-mongo.sh`, asserting which document store the
# application container's environment actually selects after each. The install is described the way install.sh describes it: its own
# project name and PIPESHUB_COMPOSE_OVERRIDES in the env file. The application is the postgres image sleeping, with a `node` shim that
# logs the drain call; the migrator's CLI is a shell step that logs in with PG_IMPORT_URL (the CLI is covered by the docstore-migrate
# suite). Env: PG_IMAGE (default pmcompose/postgres:18-pmc), MIGRATE_IMAGE (default: built here as pmcompose/migrate:pmc),
# PROJECT_PREFIX, EXTRA_COMPOSE_FILE.
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj="${PROJECT_PREFIX:-pgmig}-scripts-$$"
image="${PG_IMAGE:-pmcompose/postgres:18-pmc}"; migrate_image="${MIGRATE_IMAGE:-pmcompose/migrate:pmc}"
export PIPESHUB_ENV_FILE="$work/env" PIPESHUB_PROJECT="$proj"
dc() { $DOCKER compose --env-file "$work/env" -p "$proj" -f docker-compose.yml -f "$work/stub.yml" ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} "$@"; }
trap 'dc down -v >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
[ -n "${MIGRATE_IMAGE:-}" ] || $DOCKER build -q -f ../../backend/db/Dockerfile -t "$migrate_image" ../.. >/dev/null
tag="${image##*:}"; major="${tag%%-*}"
{ for v in ADMIN NODE CONNECTORS INDEXING QUERY JOBS MIGRATE BACKUP KV; do echo "PG_${v}_PASSWORD=$(pw)"; done
  echo "MONGO_PASSWORD=$(pw)"; echo "REDIS_PASSWORD=x"; echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${tag#*-}"
  echo "PIPESHUB_MIGRATE_IMAGE=${migrate_image%:*}"; echo "IMAGE_TAG=${migrate_image##*:}"; echo "PIPESHUB_IMAGE=${image%:*}"
  echo "COMPOSE_PROFILES=postgres,postgres-migrate"
  echo "PIPESHUB_COMPOSE_OVERRIDES=$work/stub.yml${EXTRA_COMPOSE_FILE:+:$EXTRA_COMPOSE_FILE}"; } > "$work/env"
mkdir -p "$work/out"; chmod 777 "$work/out"
# the stand-in migrator logs in as the import login the way the real one does, and records whether that worked
stub() {   # $1: the shell line run after the login attempt (-w: never prompt, as the real client does not)
  printf '%s\n' 'psql -w "$PG_IMPORT_URL" -tAc "select current_user" >> /out/login.txt 2>&1' "$1" > "$work/out/m.sh"
  { cat <<YML
services:
  docstore-migrate:
    image: $image
    entrypoint: ["sh", "/out/m.sh"]
    volumes: ["$work/out:/out"]
  redis: {profiles: !override [never]}
  qdrant: {profiles: !override [never]}
  pipeshub-ai:
    image: $image
    entrypoint: ["sleep", "infinity"]
    healthcheck: {disable: true}
    ports: !reset []
    build: !reset null
    networks: !override [pipeshub]
    volumes: !override ["$work/out/node:/usr/local/bin/node:ro", "$work/out:/out"]
    depends_on:
YML
    for d in $app_deps; do echo "      $d: !reset null"; done; } > "$work/stub.yml"
}
app_deps="$($DOCKER compose --env-file "$work/env" -p "$proj" -f docker-compose.yml config --format json | python3 -c 'import json,sys; print(" ".join(k for k in json.load(sys.stdin)["services"]["pipeshub-ai"].get("depends_on",{}) if k!="mongodb"))')"
printf '#!/bin/sh\necho "$@" >> /out/node.log\n' > "$work/out/node"; chmod +x "$work/out/node"
stub 'echo "$@" >> /out/args.txt'
store() { $DOCKER exec "$proj-pipeshub-ai-1" sh -c 'echo "${DOC_STORE:-mongo}"' | tr -d '\r'; }
# the install as it is before the move: the application is running on MongoDB
dc up -d --no-build mongodb pgbouncer pipeshub-ai >/dev/null 2>&1 || { echo "FAIL: the install did not start"; dc up -d --no-build mongodb pgbouncer pipeshub-ai 2>&1 | tail -5; exit 1; }
check "before the move the application is on MongoDB" "$(store)" mongo
role_login() { $DOCKER exec "$proj-postgres-1" psql -U postgres -d pipeshub -tAc "select rolcanlogin from pg_roles where rolname='pipeshub_import'" | tr -d '\r'; }

scripts/migrate-to-postgres.sh --dry-run --drain-wait 20 > "$work/run.log" 2>&1 && rc=0 || rc=$?
[ "$rc" -eq 0 ] || { echo "---- script output:"; tail -30 "$work/run.log"; }
check "the dry run exits 0" "$rc" 0
check "the copy was followed by a drain cancel (a dry run changes nothing)" "$(sed -n 2p "$work/out/args.txt" | tr -d '\r')" "drain --cancel"
check "the migrator logged in as the import login while it ran" "$(head -1 "$work/out/login.txt" | tr -d '\r')" pipeshub_import
check "the migrator was given the offline migrate and dry run options" "$(head -1 "$work/out/args.txt" | tr -d '\r')" "migrate --offline --dry-run"
check "the import login is closed afterwards" "$(role_login)" f
check "its password is gone from the env file" "$(grep -c '^PG_IMPORT_PASSWORD=' "$work/env" || true)" 0
dc run --rm --no-deps -T pipeshub-migrate > "$work/schema.log" 2>&1 && rc=0 || rc=$?
check "a schema run (with its surface check) passes after the script" "$rc" 0

# a failing migrator step still shuts the login and removes the password
stub 'exit 3'
scripts/migrate-to-postgres.sh > "$work/fail.log" 2>&1 && rc=0 || rc=$?
check "a failing move exits non-zero" "$([ "$rc" -ne 0 ] && echo failed)" failed
check "and the import login is closed" "$(role_login)" f
check "and its password is gone from the env file" "$(grep -c '^PG_IMPORT_PASSWORD=' "$work/env" || true)" 0

dc up -d --no-build pipeshub-ai >/dev/null 2>&1   # the earlier runs stopped it; the drain needs it running
# --flip: the application is recreated on PostgreSQL (the store is what its environment says, not what a file claims)
stub 'echo "$@" >> /out/args.txt'; rm -f "$work/out/args.txt" "$work/out/node.log"
scripts/migrate-to-postgres.sh --flip --drain-wait 20 > "$work/flip.log" 2>&1 && rc=0 || rc=$?
[ "$rc" -eq 0 ] || { echo "---- script output:"; tail -30 "$work/flip.log"; }
check "--flip exits 0" "$rc" 0
check "the running application was asked to drain" "$(head -1 "$work/out/node.log" | tr -d '\r' | cut -d' ' -f1-2)" "/app/backend/dist/scripts/docstore-migrate.js drain"
check "after --flip the application's environment selects PostgreSQL" "$(store)" postgres
check "and it connects through the pooler" "$($DOCKER exec "$proj-pipeshub-ai-1" sh -c 'echo "${PG_NODE_URL%%@*}" | cut -d: -f1; echo "${PG_NODE_URL#*@}"' | tr -d '\r' | tr '\n' ' ')" "postgresql pgbouncer:6432/pipeshub "
check "the install's override list now names the PostgreSQL override" "$(grep -c 'docker-compose.postgres-docstore.yml' "$work/env")" 1
check "the import login is closed after --flip" "$(role_login)" f

# rollback: the application is recreated on MongoDB
rm -f "$work/out/args.txt"
scripts/rollback-to-mongo.sh > "$work/rb.log" 2>&1 && rc=0 || rc=$?
[ "$rc" -eq 0 ] || { echo "---- script output:"; tail -30 "$work/rb.log"; }
check "rollback exits 0" "$rc" 0
check "the migrator was given the rollback command" "$(head -1 "$work/out/args.txt" | tr -d '\r')" "rollback"
check "after rollback the application's environment selects MongoDB" "$(store)" mongo
check "the override is gone from the install's list" "$(grep -c 'docker-compose.postgres-docstore.yml' "$work/env")" 0
check "the import login is closed after rollback" "$(role_login)" f
check "and its password is gone from the env file" "$(grep -c '^PG_IMPORT_PASSWORD=' "$work/env" || true)" 0

# a project the scripts cannot find is refused, not treated as "nothing to drain"
PIPESHUB_PROJECT="$proj-none" scripts/migrate-to-postgres.sh --dry-run > "$work/none.log" 2>&1 && rc=0 || rc=$?
check "a project with no application container is refused" "$rc" 1
exit $fail
