#!/usr/bin/env bash
# `docker compose` with the stack's own compose files, from any shell: COMPOSE_FILE when it is set (environment or .env), else
# docker-compose.yml plus the files install.sh keeps in .env as PIPESHUB_COMPOSE_OVERRIDES (it passes them with -f and ignores
# COMPOSE_FILE; install.sh:81-95 reads the last such line). Project and profiles come from .env as usual.
#   scripts/pipeshub-compose.sh exec pg-backup pgbackrest info
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -z "${COMPOSE_FILE:-}" ] && [ -f .env ] && ! grep -q '^COMPOSE_FILE=' .env; then
  overrides="$(grep '^PIPESHUB_COMPOSE_OVERRIDES=' .env | tail -1 | cut -d= -f2- || true)"   # no match is not an error (as install.sh)
  if [ -n "$overrides" ]; then export COMPOSE_FILE="docker-compose.yml:$overrides"; fi
fi
exec docker compose "$@"
