#!/usr/bin/env bash
# Creates a migrated PostgreSQL database for the integration suites that connect to a running server and writes the connection
# variables they read to stdout as `export` lines (workflows append them to $GITHUB_ENV with `sed 's/^export //'`).
#   PG_ADMIN_URL=postgresql://postgres@127.0.0.1:5432/postgres scripts/prepare_pg_test_db.sh [dbname]
# Needs: python with backend/db/requirements.txt installed (PREPARE_PG_PYTHON, default python), a superuser in PG_ADMIN_URL.
# PG_RUNTIME_ROLE (connectors|node) picks the login in PG_RUNTIME_TEST_URL. Every login role gets the password in PG_TEST_PASSWORD (default: a fixed test value; the server is a throwaway one).
set -euo pipefail
: "${PG_ADMIN_URL:?PG_ADMIN_URL is required}"
db=${1:-pipeshub_ci}
py=${PREPARE_PG_PYTHON:-python}
pw=${PG_TEST_PASSWORD:-ci-test-password}
root=$(cd "$(dirname "$0")/.." && pwd)
base=${PG_ADMIN_URL%/*}                     # postgresql://user@host:port
hostport=${base#*@}
"$py" - "$PG_ADMIN_URL" "$db" <<'PY' >&2
import sys, psycopg
with psycopg.connect(sys.argv[1], autocommit=True) as c:
    c.execute(f'DROP DATABASE IF EXISTS "{sys.argv[2]}" WITH (FORCE)')
    c.execute(f'CREATE DATABASE "{sys.argv[2]}"')
PY
export PG_ADMIN_URL="$base/$db" PG_BOOTSTRAP_EXTENSIONS=pg_trgm
for r in NODE CONNECTORS INDEXING QUERY KV JOBS MIGRATE BACKUP; do export "PG_${r}_PASSWORD=$pw"; done
cd "$root/backend"
"$py" -m db.bootstrap_roles >&2
PG_MIGRATE_URL="postgresql+psycopg://pipeshub_migrate:$pw@$hostport/$db" "$py" -m db.migrate >&2
login() { echo "postgresql://pipeshub_$1_login:$pw@$hostport/$db"; }
cat <<OUT
export PG_ADMIN_URL=$base/$db
export PG_CORE_URL=$(login connectors)
export PG_CORE_QUERY_URL=$(login query)
export PG_QUERY_URL=$(login query)
export PG_NODE_URL=$(login node)
export PG_RUNTIME_TEST_URL=$(login "${PG_RUNTIME_ROLE:-connectors}")
export PG_POOLER=none
export PG_RUNTIME_SERVICE=connectors
OUT
