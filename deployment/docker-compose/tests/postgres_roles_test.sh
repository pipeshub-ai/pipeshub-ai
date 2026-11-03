#!/usr/bin/env bash
# The postgres + pgbouncer services, started from docker-compose.yml: the roles initdb creates hold no superuser-like attribute,
# a service login can reach the database only through its own role, PgBouncer authenticates the pooled logins from the SCRAM
# verifier file (and refuses a wrong password), and the verifier file is private and holds no clear-text password.
# Needs docker; PG_IMAGE_17 / PG_IMAGE_18 name the built images (default: pmcompose/postgres:17-pmc and :18-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
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
  dc up -d --no-build postgres pgbouncer >/dev/null 2>&1
  for _ in $(seq 1 60); do [ "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pgbouncer-1" 2>/dev/null)" = healthy ] && break; sleep 2; done
  check "$major: pgbouncer is healthy" "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pgbouncer-1")" healthy

  check "$major: no pipeshub role is superuser, createrole, createdb, replication or bypassrls" \
    "$(sql "select count(*) from pg_roles where rolname like 'pipeshub\\_%' and (rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls)")" 0
  check "$major: only the poolable and operator logins can log in" \
    "$(sql "select string_agg(rolname, ',' order by rolname) from pg_roles where rolname like 'pipeshub\\_%' and rolcanlogin")" \
    "pipeshub_backup,pipeshub_connectors_login,pipeshub_indexing_login,pipeshub_jobs,pipeshub_kv_login,pipeshub_migrate,pipeshub_node_login,pipeshub_query_login,pipeshub_ro"
  check "$major: a login holds a service role by SET only, never by inheritance" \
    "$(sql "select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid join pg_roles u on u.oid=m.member where u.rolname like '%\\_login' and (m.inherit_option or not m.set_option)")" 0
  check "$major: pipeshub_ro has no password" "$(sql "select rolpassword is null from pg_authid where rolname='pipeshub_ro'")" t
  check "$major: stored verifiers are SCRAM" "$(sql "select count(*) from pg_authid where rolname like 'pipeshub\\_%\\_login' and rolpassword not like 'SCRAM-SHA-256\$%'")" 0
  check "$major: pgvector and pg_trgm sit in schema ext" "$(sql "select string_agg(extname,',' order by extname) from pg_extension e join pg_namespace n on n.oid=e.extnamespace where n.nspname='ext' and extname in ('vector','pg_trgm')")" "pg_trgm,vector"
  check "$major: public schema is closed to PUBLIC" "$(sql "select has_schema_privilege('pipeshub_node_login','public','CREATE')")" f

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
  dc down -v >/dev/null 2>&1
done
exit $fail
