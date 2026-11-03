# Sourced by migrate-to-postgres.sh and rollback-to-mongo.sh (needs DC and ENV_FILE). The copy writes with its own PostgreSQL login,
# `pipeshub_import`: NOLOGIN at all other times, enabled with the password in $ENV_FILE (read by the docstore-migrate service only) for
# the length of one run.
import_login_on() {
  touch "$ENV_FILE"
  grep -q '^PG_IMPORT_PASSWORD=' "$ENV_FILE" || echo "PG_IMPORT_PASSWORD=$(openssl rand -hex 24)" >> "$ENV_FILE"
  local pw; pw="$(sed -n 's/^PG_IMPORT_PASSWORD=//p' "$ENV_FILE" | tail -1)"
  PSQL=("${DC[@]}" exec -T postgres psql -q -v ON_ERROR_STOP=1 -U postgres -d pipeshub)
  # created NOLOGIN here when this install's database predates the role
  "${PSQL[@]}" -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_import') THEN CREATE ROLE pipeshub_import NOLOGIN; END IF; END \$\$" >/dev/null
  "${DC[@]}" run --rm pipeshub-migrate >/dev/null          # the schema (and the role's policies) at head
  printf "ALTER ROLE pipeshub_import LOGIN PASSWORD '%s'" "$pw" | "${PSQL[@]}" >/dev/null
}
import_login_off() {   # login shut and the password gone from the env file; the next move generates a new one
  "${PSQL[@]}" -c "ALTER ROLE pipeshub_import NOLOGIN" >/dev/null 2>&1 || true
  [ -f "$ENV_FILE" ] && sed -i '/^PG_IMPORT_PASSWORD=/d' "$ENV_FILE" || true
}
