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
