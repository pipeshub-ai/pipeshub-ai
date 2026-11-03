#!/bin/sh
# Adds the image's server defaults when the container runs postgres; any other command (pgbouncer) passes through.
set -e
if [ "$1" = postgres ]; then
  shift
  # The compose file mounts the data volume at /var/lib/postgresql/data and sets PGDATA to it, on both majors. A cluster anywhere
  # else on the container's filesystem (the volume mounted one level up by an override, or a cluster of the 18 image's default
  # 18/docker) would be ignored and an empty one initialised beside it, so the app would start on an empty database: refuse.
  root="${PG_VOLUME_ROOT:-/var/lib/postgresql}"
  here="${PGDATA:-$root/data}"
  # (data/pgdata is the Helm chart's layout: the volume's subPath directory belongs to root, so the cluster is created one level down.)
  for found in "$root" "$root/data" "$root/data/pgdata" "$root"/*/docker; do
    if [ -f "$found/PG_VERSION" ] && [ "$found" != "$here" ]; then
      echo "pg-entrypoint: this volume holds a PostgreSQL $(cat "$found/PG_VERSION") cluster in $found, but this container (PGDATA=$here) would start an empty one beside it. Not starting." >&2
      echo "pg-entrypoint: mount the data volume at $here and set PGDATA to it (the compose file does), and use the image of that major (PG_MAJOR=$(cat "$found/PG_VERSION"))." >&2
      exit 78
    fi
  done
  # Every password is checked before the stock entrypoint runs: a cluster initialised with one missing would be left without
  # its roles, and the next start would skip initialisation and serve it as it is.
  for v in POSTGRES_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD \
           PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD; do
    eval "val=\${$v:-}"
    # The same values go into connection URLs (docker-compose.postgres-docstore.yml), which cannot carry reserved characters.
    case "$val" in *[!A-Za-z0-9._~-]*) [ "$v" = POSTGRES_PASSWORD ] && v=PG_ADMIN_PASSWORD
      echo "pg-entrypoint: $v may contain only letters, digits and . _ ~ - (it is used in a URL). Not starting." >&2; exit 78;; esac
    if [ -z "$val" ]; then
      [ "$v" = POSTGRES_PASSWORD ] && v=PG_ADMIN_PASSWORD
      echo "pg-entrypoint: $v is empty; set it in .env. Not starting." >&2
      exit 78
    fi
  done
  # Create what is missing of the roles on every start, once the server answers on TCP (the initialisation server listens on the
  # socket only), so a volume that was left half-set-up repairs itself and PgBouncer's verifier file is rewritten. A failure is
  # logged and leaves PgBouncer unhealthy.
  roles="${PG_ROLES_SCRIPT:-/usr/local/bin/pg-roles.sh}"
  if [ -x "$roles" ]; then
    (
      for _ in $(seq 1 300); do pg_isready -q -h 127.0.0.1 -U postgres && break; sleep 1; done
      gosu postgres "$roles" || echo "pg-entrypoint: role setup failed; see the lines above" >&2
    ) &
  fi
  # pg_textsearch must be preloaded. shared_buffers is ~25% of PG_MEMORY_LIMIT (set in .env).
  set -- postgres \
    -c shared_preload_libraries=pg_stat_statements,auto_explain,pg_textsearch \
    -c max_connections="${PG_MAX_CONNECTIONS:-200}" \
    -c shared_buffers="${PG_SHARED_BUFFERS:-1GB}" \
    ${PG_AUTOVACUUM_WORK_MEM:+-c autovacuum_work_mem="$PG_AUTOVACUUM_WORK_MEM"} \
    ${PG_MAINTENANCE_WORK_MEM:+-c maintenance_work_mem="$PG_MAINTENANCE_WORK_MEM"} \
    "$@"
fi
exec docker-entrypoint.sh "$@"
