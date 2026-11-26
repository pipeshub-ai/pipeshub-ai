#!/usr/bin/env bash
# shellcheck disable=SC2034 # globals below are read by the eval'd installer functions
# ==============================================================================
# MongoDB replica set by default (Docker required).
# ==============================================================================
#   R1  the base compose mongodb service, started alone on a fresh volume,
#       reports setName rs0 and a writable primary.
#   R2  install.sh converts a standalone data volume in place: it takes a
#       backup, and every collection keeps its document count.
#   R3  running the conversion again is a no-op, whether the replica-set
#       container still exists or was removed.
#
# Uses a unique Compose project and removes its containers and volumes on exit.
#
#   bash deployment/docker-compose/tests/mongodb_replset_test.sh
# ==============================================================================
set -euo pipefail

compose_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="${compose_dir}/install.sh"
project="pipeshub-mongo-rs-${RANDOM}${RANDOM}"
work="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-mongo-rs.XXXXXX")"
mongo_user="admin"
mongo_password="mongo-rs-test-password"

cat >"${work}/.env" <<EOF
MONGO_USERNAME=${mongo_user}
MONGO_PASSWORD=${mongo_password}
REDIS_PASSWORD=mongo-rs-test-redis
EOF

compose=(docker compose --project-name "$project" --env-file "${work}/.env" --file "${compose_dir}/docker-compose.yml")

# The compose file of a release before the replica set: a standalone mongod.
cat >"${work}/standalone.yml" <<'EOF'
services:
  mongodb:
    image: mongo:${MONGO_IMAGE_TAG:-8.0.17}
    command: ["mongod", "--bind_ip_all", "--wiredTigerCacheSizeGB", "1"]
    environment:
      - MONGO_INITDB_ROOT_USERNAME=${MONGO_USERNAME}
      - MONGO_INITDB_ROOT_PASSWORD=${MONGO_PASSWORD}
    volumes:
      - mongodb_data:/data/db
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping')"]
      interval: 2s
      timeout: 5s
      retries: 30
volumes:
  mongodb_data:
    driver: local
EOF
standalone=(docker compose --project-name "$project" --env-file "${work}/.env" --file "${work}/standalone.yml")

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  "${standalone[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker rm -f "${project}-mongodb-rs-migrate" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

failures=0
pass() { printf '  ok   - %s\n' "$1"; }
fail() { printf '  FAIL - %s\n' "$1"; failures=$((failures + 1)); }
expect_eq() { # description actual expected
  if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1 (expected '$3', got '$2')"; fi
}

mongo_eval() { # service-container mongosh with root credentials
  "${compose[@]}" exec -T mongodb mongosh --quiet \
    --username "$mongo_user" --password "$mongo_password" --authenticationDatabase admin \
    --eval "$1"
}

# ── the real installer functions, run in isolation ───────────────────────────
extract_fn() { awk -v fn="$1" '$0 ~ "^"fn"\\(\\) \\{"{g=1} g{print} g&&/^\}/{exit}' "$2"; }
definitions=""
for fn in volume_exists mongo_container_ids mongo_counts_js mongo_probe_name \
          stop_mongo_probe start_mongo_probe migrate_mongo_to_replset; do
  definition="$(extract_fn "$fn" "$installer")"
  [[ -n "$definition" ]] || { echo "install.sh no longer defines ${fn}()" >&2; exit 1; }
  eval "$definition"
  definitions+="${definition}"$'\n'
done
info()    { printf '    > %s\n' "$*"; }
success() { printf '    + %s\n' "$*"; }
warn()    { printf '    ! %s\n' "$*"; }
error()   { printf '    x %s\n' "$*" >&2; }
die()     { error "$*"; exit 1; }
BOLD="" RESET=""
FLAG_YES=true
PROJECT_NAME="$project"
COMPOSE_FILE="${compose_dir}/docker-compose.yml"
ENV_FILE="${work}/.env"
MONGO_USERNAME="$mongo_user"
MONGO_BACKUP_DIR="${work}/backups"
MONGO_PROBE_WAIT_SECS=60
COMPOSE_FILES=(-f "$COMPOSE_FILE")
COMPOSE_FILES_SHOWN="${COMPOSE_FILES[*]}"

# The extracted functions read installer globals this file must set. One left unset does not fail under set -u when
# it is an array: compose then runs without -f, finds no project file and the migration dies with a misleading error.
unset_globals=()
while read -r name; do
  declare -p "$name" >/dev/null 2>&1 || unset_globals+=("$name")
done < <(grep -oE '\$(\{[A-Z][A-Z0-9_]*(\[[@*]\])?\}|[A-Z][A-Z0-9_]*)' <<<"$definitions" \
  | sed -E 's/^\$\{?([A-Z][A-Z0-9_]*).*/\1/' | sort -u)
if (( ${#unset_globals[@]} )); then
  echo "install.sh functions read globals this test does not set: ${unset_globals[*]}" >&2
  exit 1
fi

run_migration() { # log file
  (set -euo pipefail; migrate_mongo_to_replset) >"$1" 2>&1
}

echo "== R1: fresh volume starts as a writable replica-set primary"
run_migration "${work}/fresh.log" && pass "migration is a no-op without a data volume" \
  || { cat "${work}/fresh.log"; fail "migration is a no-op without a data volume"; }
[[ ! -d "$MONGO_BACKUP_DIR" ]] && pass "no backup for a fresh install" || fail "no backup for a fresh install"
"${compose[@]}" up --detach --wait mongodb >/dev/null 2>&1 || { "${compose[@]}" logs mongodb; exit 1; }
expect_eq "hello.setName" "$(mongo_eval 'print(db.hello().setName)')" "rs0"
expect_eq "hello.isWritablePrimary" "$(mongo_eval 'print(db.hello().isWritablePrimary)')" "true"
expect_eq "oplog is 1 GiB" "$(mongo_eval 'print(db.getSiblingDB("local").oplog.rs.stats().maxSize)')" "1073741824"
expect_eq "multi-document transaction commits" "$(mongo_eval '
  const s = db.getMongo().startSession(); const d = s.getDatabase("rs_test");
  s.startTransaction(); d.a.insertOne({_id: 1}); d.b.insertOne({_id: 1}); s.commitTransaction();
  const n = d.a.countDocuments() + d.b.countDocuments(); d.dropDatabase(); print(n);')" "2"
"${compose[@]}" down --volumes >/dev/null 2>&1

echo "== R2: standalone volume with data converts in place"
"${standalone[@]}" up --detach --wait mongodb >/dev/null 2>&1 || { "${standalone[@]}" logs mongodb; exit 1; }
"${standalone[@]}" exec -T mongodb mongosh --quiet \
  --username "$mongo_user" --password "$mongo_password" --authenticationDatabase admin --eval '
  const es = db.getSiblingDB("es");
  es.users.insertMany(Array.from({length: 25}, (_, i) => ({_id: i, email: "u" + i + "@example.com"})));
  es.orgs.insertMany([{name: "a"}, {name: "b"}, {name: "c"}]);
  es.users.createIndex({email: 1}, {unique: true});
  es.createCollection("empty");
  const other = db.getSiblingDB("other");
  other.items.insertMany(Array.from({length: 1000}, (_, i) => ({n: i})));
  other.createView("big_items", "items", [{$match: {n: {$gte: 500}}}]);
' >/dev/null
expected_counts="$("${standalone[@]}" exec -T mongodb mongosh --quiet --eval "$(mongo_counts_js)" \
  --username "$mongo_user" --password "$mongo_password" --authenticationDatabase admin)"
expect_eq "seeded standalone is not a replica set" \
  "$("${standalone[@]}" exec -T mongodb mongosh --quiet --eval 'print(db.hello().setName === undefined)')" "true"

if run_migration "${work}/migrate.log"; then pass "migration succeeds"; else fail "migration succeeds"; fi
cat "${work}/migrate.log"
grep -q "converted to replica set rs0" "${work}/migrate.log" && pass "migration reports the verified conversion" \
  || fail "migration reports the verified conversion"
backups=("${MONGO_BACKUP_DIR}"/*.archive.gz)
[[ -s "${backups[0]}" ]] && pass "backup archive written" || fail "backup archive written"
expect_eq "backup is owner-only" "$(ls -l "${backups[0]}" | cut -c1-10)" "-rw-------"
"${compose[@]}" exec -T mongodb mongorestore --archive --gzip --dryRun --quiet \
  --username "$mongo_user" --password "$mongo_password" --authenticationDatabase admin <"${backups[0]}" \
  && pass "backup archive is restorable (dry run)" || fail "backup archive is restorable (dry run)"
expect_eq "hello.setName after conversion" "$(mongo_eval 'print(db.hello().setName)')" "rs0"
expect_eq "writable primary after conversion" "$(mongo_eval 'print(db.hello().isWritablePrimary)')" "true"
actual_counts="$(mongo_eval "$(mongo_counts_js)")"
expect_eq "every collection keeps its count" "$actual_counts" "$expected_counts"
expect_eq "es.users count" "$(mongo_eval 'print(db.getSiblingDB("es").users.countDocuments())')" "25"
expect_eq "other.items count" "$(mongo_eval 'print(db.getSiblingDB("other").items.countDocuments())')" "1000"
expect_eq "view still resolves" "$(mongo_eval 'print(db.getSiblingDB("other").big_items.countDocuments())')" "500"
expect_eq "unique index kept" \
  "$(mongo_eval 'print(db.getSiblingDB("es").users.getIndexes().some((i) => i.unique === true))')" "true"
[[ -z "$(docker ps -aq --filter "name=^${project}-mongodb-rs-migrate$")" ]] && pass "probe container removed" \
  || fail "probe container removed"

echo "== R3: rerunning the conversion is a no-op"
run_migration "${work}/rerun.log" && pass "rerun with the replica-set container" || fail "rerun with the replica-set container"
"${compose[@]}" rm --stop --force mongodb >/dev/null 2>&1
run_migration "${work}/rerun-no-container.log" && pass "rerun with the container removed" \
  || fail "rerun with the container removed"
cat "${work}/rerun.log" "${work}/rerun-no-container.log"
backups=("${MONGO_BACKUP_DIR}"/*.archive.gz)
expect_eq "no further backups" "${#backups[@]}" "1"
! grep -q "Backup" "${work}/rerun.log" "${work}/rerun-no-container.log" && pass "no backup prompt on rerun" \
  || fail "no backup prompt on rerun"
"${compose[@]}" up --detach --wait mongodb >/dev/null 2>&1 || { "${compose[@]}" logs mongodb; exit 1; }
expect_eq "counts unchanged after rerun" "$(mongo_eval "$(mongo_counts_js)")" "$expected_counts"
expect_eq "still primary after rerun" "$(mongo_eval 'print(db.hello().isWritablePrimary)')" "true"

if (( failures > 0 )); then
  echo "MongoDB replica-set tests: ${failures} failure(s)" >&2
  exit 1
fi
echo "MongoDB replica-set tests passed"
