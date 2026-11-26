#!/usr/bin/env bash
# DEV-ONLY: the e2e lane's PgBouncer (docker-compose.pg.yml) in transaction mode, as an install runs it in front of the
# document store: the logins the app uses (node, jobs) with their passwords, and the targeted reset of
# backend/python/app/core/db/README.md (no client resets its session; the pooler does, after every transaction).
set -euo pipefail
dir=/tmp/pgbouncer
mkdir -p "$dir"
umask 077
printf '"pipeshub_node_login" "%s"\n"pipeshub_jobs" "%s"\n' "$PG_NODE_PASSWORD" "$PG_JOBS_PASSWORD" >"$dir/users.txt"
cat >"$dir/pgbouncer.ini" <<INI
[databases]
${PG_DATABASE} = host=postgres port=5432 dbname=${PG_DATABASE}
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
unix_socket_dir =
pool_mode = transaction
auth_type = scram-sha-256
auth_file = $dir/users.txt
server_reset_query = RESET ALL; SET SESSION AUTHORIZATION DEFAULT; CLOSE ALL; DISCARD TEMP; UNLISTEN *; SELECT pg_advisory_unlock_all(); DEALLOCATE ALL
server_reset_query_always = 1
max_prepared_statements = 200
server_login_retry = 2
max_client_conn = 400
default_pool_size = 20
max_db_connections = 60
ignore_startup_parameters = extra_float_digits,options
INI
exec pgbouncer "$dir/pgbouncer.ini"
