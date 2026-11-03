#!/usr/bin/env bash
# The postgres + pgbouncer services, started from docker-compose.yml: the roles initdb creates hold no superuser-like attribute,
# a service login can reach the database only through its own role, PgBouncer authenticates the pooled logins from the SCRAM
# verifier file (and refuses a wrong password), and the verifier file is private and holds no clear-text password.
# Test-only environment: PROJECT_PREFIX names the compose projects; EXTRA_COMPOSE_FILE adds one compose file (for example to use a
# network of your own on a host whose address pools are used up). Both change nothing when unset.
# Needs docker; PG_IMAGE_17 / PG_IMAGE_18 name the built images (default: pmcompose/postgres:17-pmc and :18-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj=""; major=""
cleanup() { if [ -n "$proj" ]; then $DOCKER compose --env-file "$work/env$major" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} down -v >/dev/null 2>&1 || true; $DOCKER rm -f "$proj-empty" >/dev/null 2>&1 || true; $DOCKER volume rm "$proj-empty-data" >/dev/null 2>&1 || true; fi; rm -rf "$work"; }
trap cleanup EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD"

for major in 17 18; do
  image_var="PG_IMAGE_$major"; default="pmcompose/postgres:$major-pmc"; image="${!image_var:-$default}"
  proj="${PROJECT_PREFIX:-pgimg}-roles-$major-$$"
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"; echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=postgres"; } > "$work/env$major"
  . "$work/env$major"
  dc() { $DOCKER compose --env-file "$work/env$major" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} "$@"; }
  sql() { $DOCKER exec "$proj-postgres-1" psql -U postgres -d pipeshub -tAc "$1"; }
  healthy() { for _ in $(seq 1 60); do [ "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-$1-1" 2>/dev/null)" = healthy ] && return 0; sleep 2; done; return 1; }
  nroles() { sql "select count(*) from pg_roles where rolname like 'pipeshub\\_%'"; }

  # An empty password stops the start before anything is written to the data volume.
  for v in PG_NODE_PASSWORD PG_ADMIN_PASSWORD; do
    $DOCKER volume create "$proj-empty-data" >/dev/null
    env_args=(); for w in $pwvars; do if [ "$w" = "$v" ]; then env_args+=(-e "$w="); else env_args+=(-e "$w=${!w}"); fi; done
    set +e
    out="$($DOCKER run --rm --name "$proj-empty" -v "$proj-empty-data:/var/lib/postgresql/data" -e PGDATA=/var/lib/postgresql/data -e POSTGRES_DB=pipeshub \
      "${env_args[@]/PG_ADMIN_PASSWORD=/POSTGRES_PASSWORD=}" "$image" 2>&1)"; code=$?
    set -e
    check "$major: empty $v exits 78 with a message" "$code $(echo "$out" | grep -c 'is empty; set it in .env')" "78 1"
    check "$major: empty $v leaves the data volume empty" "$($DOCKER run --rm -v "$proj-empty-data:/d" --entrypoint sh "$image" -c 'ls -A /d | wc -l')" 0
    $DOCKER volume rm "$proj-empty-data" >/dev/null
  done

  if ! dc up -d --no-build postgres pgbouncer > "$work/up.log" 2>&1; then echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; continue; fi
  healthy pgbouncer || { echo "FAIL $major: pgbouncer did not become healthy"; dc logs --tail 20 postgres pgbouncer; fail=1; }
  check "$major: pgbouncer is healthy" "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pgbouncer-1")" healthy
  check "$major: no host port is published" "$($DOCKER inspect -f '{{json .NetworkSettings.Ports}}' "$proj-postgres-1" "$proj-pgbouncer-1" | grep -c HostPort)" 0

  check "$major: no pipeshub role is superuser, createrole, createdb, replication or bypassrls" \
    "$(sql "select count(*) from pg_roles where rolname like 'pipeshub\\_%' and (rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls)")" 0
  check "$major: only the poolable and operator logins can log in" \
    "$(sql "select string_agg(rolname, ',' order by rolname) from pg_roles where rolname like 'pipeshub\\_%' and rolcanlogin")" \
    "pipeshub_backup,pipeshub_connectors_login,pipeshub_indexing_login,pipeshub_jobs,pipeshub_kv_login,pipeshub_migrate,pipeshub_node_login,pipeshub_query_login,pipeshub_ro"
  check "$major: a login holds a service role by SET only, never by inheritance" \
    "$(sql "select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid join pg_roles u on u.oid=m.member where u.rolname like '%\\_login' and (m.inherit_option or not m.set_option)")" 0
  check "$major: pipeshub_ro has no password" "$(sql "select rolpassword is null from pg_authid where rolname='pipeshub_ro'")" t
  check "$major: stored verifiers are SCRAM" "$(sql "select count(*) from pg_authid where rolname like 'pipeshub\\_%' and rolpassword is not null and rolpassword not like 'SCRAM-SHA-256\$%'")" 0
  check "$major: pg_trgm sits in schema ext" "$(sql "select string_agg(extname,',' order by extname) from pg_extension e join pg_namespace n on n.oid=e.extnamespace where n.nspname='ext' and extname in ('vector','pg_trgm')")" "pg_trgm"
  check "$major: public schema is closed to PUBLIC" "$(sql "select has_schema_privilege('pipeshub_node_login','public','CREATE')")" f
  check "$major: connections from other hosts need SCRAM" "$($DOCKER exec "$proj-postgres-1" sh -c "grep -E '^host +all +all +all ' /var/lib/postgresql/data/pg_hba.conf | grep -c scram-sha-256")" 1

  # PgBouncer: right password in, wrong password out, a login not in the verifier file out
  via() { $DOCKER exec -e PGPASSWORD="$2" "$proj-postgres-1" psql -h pgbouncer -p 6432 -U "$1" -d pipeshub -tAc 'select current_user' 2>&1; }
  check "$major: pgbouncer accepts the node login" "$(via pipeshub_node_login "$PG_NODE_PASSWORD")" pipeshub_node_login
  case "$(via pipeshub_node_login wrong)" in *"authentication failed"*|*"password authentication"*) r=refused;; *) r=accepted;; esac
  check "$major: pgbouncer refuses a wrong password" "$r" refused
  case "$(via pipeshub_migrate "$PG_MIGRATE_PASSWORD")" in *pipeshub_migrate*) r=accepted;; *) r=refused;; esac
  check "$major: the migrate login is not pooled" "$r" refused
  check "$major: the verifier file is private to postgres" \
    "$($DOCKER exec "$proj-postgres-1" stat -c '%U %a' /pgbouncer-auth/userlist.txt)" "postgres 600"
  check "$major: the verifier file holds no clear-text password" "$($DOCKER exec "$proj-postgres-1" grep -c "$PG_NODE_PASSWORD" /pgbouncer-auth/userlist.txt || true)" 0

  # No clear-text password reaches the server's statement statistics, its log or any file in the data directory.
  sql "create extension if not exists pg_stat_statements" >/dev/null
  leaks=0
  for v in $pwvars; do
    p="${!v}"
    n="$(sql "select count(*) from pg_stat_statements where query like '%$p%'")"
    f="$($DOCKER exec -e P="$p" "$proj-postgres-1" sh -c 'grep -rlF -- "$P" /var/lib/postgresql/data 2>/dev/null | wc -l')"
    l="$(dc logs postgres pgbouncer 2>&1 | grep -cF -- "$p" || true)"
    leaks=$((leaks + n + f + l))
  done
  check "$major: no clear-text password in pg_stat_statements, the data directory or the logs" "$leaks" 0

  # A volume left without its roles (an interrupted first start) gets them on the next start; the verifier file is rebuilt too.
  before="$(nroles)"
  sql "do \$\$ declare r record; begin drop schema if exists ext cascade; for r in select rolname from pg_roles where rolname like 'pipeshub\\_%' loop execute format('drop owned by %I', r.rolname); end loop; for r in select rolname from pg_roles where rolname like 'pipeshub\\_%' loop execute format('drop role %I', r.rolname); end loop; end \$\$" >/dev/null 2>&1
  check "$major: setup for the repair test left no pipeshub role" "$(nroles)" 0
  $DOCKER exec "$proj-postgres-1" rm -f /pgbouncer-auth/userlist.txt
  $DOCKER restart "$proj-postgres-1" >/dev/null
  for _ in $(seq 1 60); do [ "$(nroles 2>/dev/null || echo 0)" = "$before" ] && break; sleep 2; done
  check "$major: a restart gives a volume without roles its roles back" "$(nroles)" "$before"
  for _ in $(seq 1 30); do $DOCKER exec "$proj-postgres-1" test -s /pgbouncer-auth/userlist.txt && break; sleep 1; done
  $DOCKER restart "$proj-pgbouncer-1" >/dev/null
  healthy pgbouncer || true
  check "$major: pgbouncer authenticates the node login again after the repair" "$(via pipeshub_node_login "$PG_NODE_PASSWORD")" pipeshub_node_login
  dc down -v >/dev/null 2>&1
  proj=""
done
exit $fail
