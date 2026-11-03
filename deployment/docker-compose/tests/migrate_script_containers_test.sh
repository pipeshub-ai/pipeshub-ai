#!/usr/bin/env bash
# scripts/migrate-to-postgres.sh against real containers (MongoDB, PostgreSQL, PgBouncer and the schema job): the import login is closed
# before and after, open (and usable by the copy) while the one-shot migrator runs, its password is gone from the env file afterwards,
# and the surface check of a following schema run passes. The migrator's CLI itself is stood in for by a shell step in the postgres image
# that logs in with PG_IMPORT_URL; the CLI is covered by the docstore-migrate suite. Env: PG_IMAGE (default pmcompose/postgres:18-pmc),
# MIGRATE_IMAGE (default: built here as pmcompose/migrate:pmc), PROJECT_PREFIX, EXTRA_COMPOSE_FILE.
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj="${PROJECT_PREFIX:-pgmig}-scripts-$$"
image="${PG_IMAGE:-pmcompose/postgres:18-pmc}"; migrate_image="${MIGRATE_IMAGE:-pmcompose/migrate:pmc}"
export PIPESHUB_ENV_FILE="$work/env" PIPESHUB_COMPOSE_FILES="--env-file $work/env -p $proj -f docker-compose.yml -f $work/stub.yml ${EXTRA_COMPOSE_FILE:+-f $EXTRA_COMPOSE_FILE}"
dc() { $DOCKER compose $PIPESHUB_COMPOSE_FILES "$@"; }
trap 'dc down -v >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
[ -n "${MIGRATE_IMAGE:-}" ] || $DOCKER build -q -f ../../backend/db/Dockerfile -t "$migrate_image" ../.. >/dev/null
tag="${image##*:}"; major="${tag%%-*}"
{ for v in ADMIN NODE CONNECTORS INDEXING QUERY JOBS MIGRATE BACKUP KV; do echo "PG_${v}_PASSWORD=$(pw)"; done
  echo "MONGO_PASSWORD=$(pw)"; echo "REDIS_PASSWORD=x"; echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${tag#*-}"
  echo "PIPESHUB_MIGRATE_IMAGE=${migrate_image%:*}"; echo "IMAGE_TAG=${migrate_image##*:}"; echo "PIPESHUB_IMAGE=${image%:*}"
  echo "COMPOSE_PROFILES=postgres,postgres-migrate"; } > "$work/env"
mkdir -p "$work/out"; chmod 777 "$work/out"
# the stand-in migrator logs in as the import login the way the real one does, and records whether that worked
stub() {   # $1: the shell line run after the login attempt (-w: never prompt, as the real client does not)
  printf '%s\n' 'psql -w "$PG_IMPORT_URL" -tAc "select current_user" >> /out/login.txt 2>&1' "$1" > "$work/out/m.sh"
  cat > "$work/stub.yml" <<YML
services:
  docstore-migrate:
    image: $image
    entrypoint: ["sh", "/out/m.sh"]
    volumes: ["$work/out:/out"]
YML
}
stub 'echo "$@" >> /out/args.txt'
role_login() { dc exec -T postgres psql -U postgres -d pipeshub -tAc "select rolcanlogin from pg_roles where rolname='pipeshub_import'" | tr -d '\r'; }

scripts/migrate-to-postgres.sh --dry-run > "$work/run.log" 2>&1 && rc=0 || rc=$?
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
exit $fail
