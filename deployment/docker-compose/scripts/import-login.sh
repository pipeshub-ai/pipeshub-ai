# Sourced by migrate-to-postgres.sh and rollback-to-mongo.sh; run from deployment/docker-compose/.
#
# Which install the scripts act on is read the way install.sh does: the env file (PIPESHUB_ENV_FILE, default .env), the project name
# (PIPESHUB_PROJECT, else COMPOSE_PROJECT_NAME in the env file, else pipeshub-ai) and docker-compose.yml plus the files listed in
# PIPESHUB_COMPOSE_OVERRIDES (colon-separated). As in install.sh an exported PIPESHUB_COMPOSE_OVERRIDES wins over the env file, so
# unset it before a move: the scripts change the env file's list, and an exported one would hide the change from the next run.
# The scripts also keep COMPOSE_FILE in the env file equal to that list, so a plain `docker compose up -d` in this directory uses it.
#
# After a kill -9 (nothing can clean up): run migrate-to-postgres.sh again, which closes the import login when it ends; if the drain is
# stuck, `docker compose run --rm --no-deps docstore-migrate drain --cancel` (same -p, --env-file and profiles) accepts chat turns again.
#
# The copy writes with its own PostgreSQL login, `pipeshub_import`: NOLOGIN at all other times, enabled with the password in $ENV_FILE
# (read by the docstore-migrate service only) for the length of one run.
POSTGRES_OVERRIDE=docker-compose.postgres-docstore.yml
ENV_FILE="${PIPESHUB_ENV_FILE:-.env}"
env_get() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | tr -d '\r' || true; }   # a CRLF env file is read as it is meant
env_set() {   # KEY VALUE: replaces the line; the file is created private and a missing final newline is not glued to the new line
  [ -f "$ENV_FILE" ] || (umask 077; : > "$ENV_FILE")
  [ -z "$(tail -c1 "$ENV_FILE")" ] || echo >> "$ENV_FILE"
  sed -i "/^$1=/d" "$ENV_FILE"; printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
}
compose_setup() {   # sets DC (an array: paths may hold spaces) and PSQL
  OVERRIDES="${OVERRIDES-${PIPESHUB_COMPOSE_OVERRIDES:-$(env_get PIPESHUB_COMPOSE_OVERRIDES)}}"
  local project="${PIPESHUB_PROJECT:-$(env_get COMPOSE_PROJECT_NAME)}" o ov
  DC=(docker compose --env-file "$ENV_FILE" -p "${project:-pipeshub-ai}" -f docker-compose.yml)
  IFS=':' read -r -a ov <<< "$OVERRIDES"
  for o in "${ov[@]}"; do [ -n "$o" ] && DC+=(-f "$o"); done
  PSQL=("${DC[@]}" exec -T postgres psql -q -v ON_ERROR_STOP=1 -U postgres -d pipeshub)
}
require_app() {   # a wrong project or file list must not look like "nothing to drain"
  [ -n "$("${DC[@]}" ps -a -q pipeshub-ai 2>/dev/null)" ] || {
    echo "No pipeshub-ai container found for this install (${DC[*]:2:4}). Run from the install's deployment/docker-compose directory;" >&2
    echo "set PIPESHUB_PROJECT, PIPESHUB_ENV_FILE and PIPESHUB_COMPOSE_OVERRIDES if the install uses a project name, env file or overrides of its own." >&2
    exit 1; }
}
app_services() {   # every service of the install except the stores, the postgres pieces and the one-shot jobs
  "${DC[@]}" config --services | grep -Ev '^(mongodb|postgres|pgbouncer|pipeshub-migrate|pipeshub-roles|docstore-follow|docstore-reverse|pipeshub-placement|pipeshub-fold|pipeshub-maintain|docstore-migrate|redis|neo4j|arango|qdrant|etcd|zookeeper|kafka-1)$' || true
}
use_postgres_store() {   # add or remove the override that makes the application use PostgreSQL, and keep it for install.sh
  local o new="" ov
  IFS=':' read -r -a ov <<< "$OVERRIDES"
  for o in "${ov[@]}"; do [ -n "$o" ] && [ "$o" != "$POSTGRES_OVERRIDE" ] && new="${new:+$new:}$o"; done
  [ "$1" = on ] && new="${new:+$new:}$POSTGRES_OVERRIDE"
  OVERRIDES="$new"; env_set PIPESHUB_COMPOSE_OVERRIDES "$new"
  local sep; sep="$(env_get COMPOSE_PATH_SEPARATOR)"; sep="${sep:-:}"   # Compose's own variables, so every compose command here agrees
  env_set COMPOSE_FILE "docker-compose.yml${new:+$sep${new//:/$sep}}"
  if [ "$1" = on ]; then   # the override depends on services of both profiles
    local p prof; prof="$(env_get COMPOSE_PROFILES)"
    for p in postgres postgres-migrate; do case ",$prof," in *",$p,"*) ;; *) prof="${prof:+$prof,}$p" ;; esac; done
    env_set COMPOSE_PROFILES "$prof"
  fi
  compose_setup
}
import_login_on() {
  [ -n "$(env_get PG_IMPORT_PASSWORD)" ] || env_set PG_IMPORT_PASSWORD "$(openssl rand -hex 24)"
  local pw; pw="$(env_get PG_IMPORT_PASSWORD)"
  # created NOLOGIN here when this install's database predates the role
  "${PSQL[@]}" -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_import') THEN CREATE ROLE pipeshub_import NOLOGIN; END IF; END \$\$" >/dev/null
  "${DC[@]}" run --rm pipeshub-migrate >/dev/null          # the schema (and the role's policies) at head
  printf "ALTER ROLE pipeshub_import LOGIN PASSWORD '%s'" "$pw" | "${PSQL[@]}" >/dev/null
}
import_login_off() {   # login shut and the password gone from the env file; the next move generates a new one
  if "${PSQL[@]}" -c "ALTER ROLE pipeshub_import NOLOGIN" >/dev/null 2>&1; then
    [ -f "$ENV_FILE" ] && sed -i '/^PG_IMPORT_PASSWORD=/d' "$ENV_FILE" || true
  else   # the password stays in the env file on purpose: the login may still be open and the next run needs it
    [ -n "${OFF_WARNED:-}" ] || echo "WARNING: could not close the PostgreSQL import login. Close it yourself: ${DC[*]} exec -T postgres psql -U postgres -d pipeshub -c 'ALTER ROLE pipeshub_import NOLOGIN' (its password is kept in the env file on purpose)" >&2
    OFF_WARNED=1; return 1
  fi
}
