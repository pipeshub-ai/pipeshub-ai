#!/usr/bin/env bash
# DEV-ONLY: the e2e lane's migrate job (docker-compose.pg.yml `pg-migrate`), as an install's: the administrator's
# role layout and extensions (db.bootstrap_roles, the only step that needs the superuser), then the schema to every
# branch's head (db.migrate, the migrate login, on a direct address: it refuses a pooler). Runs the tree's backend/db
# on the migration tool's own packages (backend/db/requirements.lock, E2E_PG_MIGRATE_SITE), never the app's.
# Only the node, jobs and migrate logins get a password; the others are created without one, and with the image's
# scram-sha-256 rule for every address they cannot sign in (the lane runs no process as them).
set -euo pipefail
export PYTHONPATH=/site:/backend PYTHONDONTWRITEBYTECODE=1
cd /backend
PG_ADMIN_URL="postgresql://postgres:${PG_ADMIN_PASSWORD}@postgres:5432/${PG_DATABASE}" \
PG_BOOTSTRAP_EXTENSIONS=vector,pg_trgm PG_BOOTSTRAP_ALLOW_NO_PASSWORD=1 \
  python -m db.bootstrap_roles
PG_MIGRATE_URL="postgresql+psycopg://pipeshub_migrate:${PG_MIGRATE_PASSWORD}@postgres:5432/${PG_DATABASE}" \
  python -m db.migrate
