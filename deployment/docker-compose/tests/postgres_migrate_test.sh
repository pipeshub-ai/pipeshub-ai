#!/usr/bin/env bash
# The `postgres-migrate` job and the document-store override, against the real images: the job waits for a healthy postgres, builds the
# schema as the migrate login, is idempotent (a second run on a database at head changes nothing and exits 0), leaves the catalog check
# clean, and the node login can reach the schema through PgBouncer. A wrong migrate password fails the job (non-zero) and leaves the
# schema alone. The override makes the app wait for the job and for PgBouncer, and refuses to render without its passwords.
# Test-only environment: PROJECT_PREFIX names the compose project; EXTRA_COMPOSE_FILE adds one compose file (for example to use a
# network of your own on a host whose address pools are used up). Both change nothing when unset.
# Needs docker; PG_IMAGE_<major> names the built postgres images (default pmcompose/postgres:<major>-pmc) and MIGRATE_IMAGE the
# migrate image (default: built here from backend/db/Dockerfile as pmcompose/migrate:pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj=""; major=""
dc() { $DOCKER compose --env-file "$work/env$major" -p "$proj" -f docker-compose.yml -f docker-compose.postgres-docstore.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} "$@"; }
cleanup() { [ -n "$proj" ] && dc down -v >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD"
migrate_image="${MIGRATE_IMAGE:-pmcompose/migrate:pmc}"
if [ -z "${MIGRATE_IMAGE:-}" ]; then $DOCKER build -q -f ../../backend/db/Dockerfile -t "$migrate_image" ../.. >/dev/null; fi

for major in 17 18; do
  image_var="PG_IMAGE_$major"; image="${!image_var:-pmcompose/postgres:$major-pmc}"
  proj="${PROJECT_PREFIX:-pgimg}-migrate-$major-$$"
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"
    echo "PIPESHUB_MIGRATE_IMAGE=${migrate_image%:*}"; echo "IMAGE_TAG=${migrate_image##*:}"; echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=postgres,postgres-migrate"; } > "$work/env$major"
  . "$work/env$major"
  sql() { $DOCKER exec "$proj-postgres-1" psql -U postgres -d pipeshub -tAc "$1"; }
  wait_exit() { for _ in $(seq 1 120); do [ "$($DOCKER inspect -f '{{.State.Status}}' "$proj-pipeshub-migrate-1" 2>/dev/null)" = exited ] && return 0; sleep 2; done; return 1; }

  # Ordering: starting only the job brings up postgres first and waits for it to be healthy.
  if ! dc up -d --no-build pipeshub-migrate > "$work/up.log" 2>&1; then echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; continue; fi
  wait_exit || { echo "FAIL $major: the migrate job did not finish"; dc logs --tail 30 pipeshub-migrate; fail=1; continue; }
  check "$major: the migrate job exits 0" "$($DOCKER inspect -f '{{.State.ExitCode}}' "$proj-pipeshub-migrate-1")" 0
  check "$major: the schema is built (kernel.schema_epoch has rows)" "$([ "$(sql 'select count(*) from kernel.schema_epoch')" -gt 0 ] && echo yes)" yes
  check "$major: the catalog check is clean" "$(sql 'select count(*) from kernel.rls_violations')" 0
  epochs="$(sql "select string_agg(branch||':'||epoch, ',' order by branch) from kernel.schema_epoch")"

  # Idempotent: a second run on a database at head exits 0 and changes nothing.
  dc run --rm --no-deps -T pipeshub-migrate > "$work/second.log" 2>&1 && rc=0 || rc=$?
  check "$major: a second run exits 0" "$rc" 0
  check "$major: a second run changes no epoch" "$(sql "select string_agg(branch||':'||epoch, ',' order by branch) from kernel.schema_epoch")" "$epochs"

  # The node login reaches the schema through PgBouncer and may switch to its service role.
  dc up -d --no-build pgbouncer >/dev/null 2>&1
  for _ in $(seq 1 60); do [ "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pgbouncer-1" 2>/dev/null)" = healthy ] && break; sleep 2; done
  check "$major: the node login works through pgbouncer after the migration" \
    "$($DOCKER exec -e PGPASSWORD="$PG_NODE_PASSWORD" "$proj-postgres-1" psql -h pgbouncer -p 6432 -U pipeshub_node_login -d pipeshub -tAc 'set role pipeshub_node; select current_user' | tail -1)" pipeshub_node

  # A wrong migrate password fails the job and leaves the schema as it was.
  PG_MIGRATE_PASSWORD=wrong dc run --rm --no-deps -T pipeshub-migrate > "$work/bad.log" 2>&1 && rc=0 || rc=$?
  check "$major: a wrong migrate password fails the job" "$([ "$rc" -ne 0 ] && echo failed)" failed
  check "$major: and the schema is untouched" "$(sql "select string_agg(branch||':'||epoch, ',' order by branch) from kernel.schema_epoch")" "$epochs"

  # The override: the app waits for PgBouncer and for a successful job, and talks to PgBouncer as the node and jobs logins.
  cfg="$(dc config 2>&1)"
  check "$major: the app waits for the migration job" "$(echo "$cfg" | awk '/^  pipeshub-ai:/{a=1} a&&/pipeshub-migrate:/{getline; print $2; exit}')" service_completed_successfully
  check "$major: the app uses the node login through pgbouncer" "$(echo "$cfg" | grep -c "PG_NODE_URL: postgresql://pipeshub_node_login:.*@pgbouncer:6432/pipeshub")" 1
  check "$major: the app uses the jobs login through pgbouncer" "$(echo "$cfg" | grep -c "PG_JOBS_URL: postgresql://pipeshub_jobs:.*@pgbouncer:6432/pipeshub")" 1
  dc down -v >/dev/null 2>&1
  proj=""
done

# Without its passwords the override does not render at all (no app with an empty login).
proj="${PROJECT_PREFIX:-pgimg}-migrate-cfg-$$"; major=cfg
grep -v '^PG_JOBS_PASSWORD=' "$work/env17" > "$work/envcfg"
out="$(dc config 2>&1 || true)"
check "an empty PG_JOBS_PASSWORD stops the override from rendering" "$(echo "$out" | grep -c 'PG_JOBS_PASSWORD is empty or unset')" 1
proj=""
exit $fail
