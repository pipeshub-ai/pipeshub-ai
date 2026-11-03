#!/usr/bin/env bash
# The move scripts' ordering and failure handling, against a stand-in `docker` that logs its calls (no containers are started):
# a failing `up -d` after the move must not touch the fences (it used to run `drain --cancel`, un-retiring MongoDB); a failure before
# the move ends the drain; the import login is shut (and its password gone) before the final `up -d`; --flip adds the PostgreSQL
# override to the install's override list and rollback removes it; the install's project, env file and overrides reach every docker
# call (paths with spaces included); a project with no application container is refused; a SIGTERM ends the drain; a login that cannot
# be closed is reported; bad options are usage errors.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin" "$W/with space"
cat > "$W/bin/docker" <<STUB
#!/bin/sh
echo "\$*" | sed -E 's/^compose --env-file .* -p [^ ]+ -f docker-compose.yml( -f [^ ]+)*/compose/' >> "$W/calls.log"
case "\$*" in
  *"ps -a -q pipeshub-ai"*) [ -f "$W/noapp" ] || echo abc123 ;;
  *"ps --status running"*) [ -f "$W/slow" ] && echo pipeshub-ai ;;
  *"exec -T pipeshub-ai"*) [ -f "$W/slow" ] && { touch "$W/draining"; sleep 30; } ;;
  *"config --services"*) printf 'mongodb\npostgres\npgbouncer\npipeshub-migrate\ndocstore-migrate\npipeshub-ai\nkafka-1\n' ;;
  *"migrate --offline"*) [ -f "$W/failmove" ] && exit 1 ;;
  *"NOLOGIN"*) [ -f "$W/failoff" ] && exit 1 ;;
  *" up -d") [ -f "$W/failup" ] && exit 1 ;;
esac
exit 0
STUB
chmod +x "$W/bin/docker"
run() { local env="$1"; shift; (cd "$HERE/.." && PATH="$W/bin:$PATH" PIPESHUB_ENV_FILE="$env" "$@" >"$W/out" 2>&1); }
fail() { echo "FAIL: $1" >&2; [ -f "$W/out" ] && tail -5 "$W/out" >&2; exit 1; }
pos() { grep -n "$1" "$W/calls.log" | "${2:-head}" -1 | cut -d: -f1; }
reset() { rm -f "$W"/noapp "$W"/slow "$W"/failmove "$W"/failoff "$W"/failup "$W"/draining; : > "$W/calls.log"; }

# 1. a failing final `up -d` after the move does not touch the fence, and the env file is switched
reset; touch "$W/failup"; printf 'PIPESHUB_COMPOSE_OVERRIDES=docker-compose.mongo-replset.yml' > "$W/env"   # no final newline
run "$W/env" scripts/migrate-to-postgres.sh --flip && fail "the script should have failed at the final up -d"
grep -q "migrate --offline" "$W/calls.log" || fail "the move did not run"
sed -n '/migrate --offline/,$p' "$W/calls.log" | grep -q "drain --cancel" && fail "a failure after the move touched the fence"
grep -qx 'PIPESHUB_COMPOSE_OVERRIDES=docker-compose.mongo-replset.yml:docker-compose.postgres-docstore.yml' "$W/env" || fail "the PostgreSQL override was not added after the existing one"
grep -q '^COMPOSE_PROFILES=postgres,postgres-migrate$' "$W/env" || fail "the two profiles were not added"
# 2. a failure before the move still ends the drain
reset; touch "$W/failmove"; : > "$W/env2"
run "$W/env2" scripts/migrate-to-postgres.sh && fail "should have failed"
grep -q "drain --cancel" "$W/calls.log" || fail "a failed move did not cancel the drain"
# 3. flip with a working up -d: the login is shut before it, no password is left, override added; paths with spaces and a project name are passed through
reset; : > "$W/with space/env"; echo "COMPOSE_PROJECT_NAME=second" > "$W/with space/env"
run "$W/with space/env" scripts/migrate-to-postgres.sh --flip || fail "the move with a working up -d should exit 0"
off="$(pos 'ALTER ROLE pipeshub_import NOLOGIN')"; up="$(pos '^compose up -d$' tail)"
[ -n "$off" ] && [ -n "$up" ] && [ "$off" -lt "$up" ] || fail "the import login was not shut before the final up -d ($off/$up)"
grep -q '^PG_IMPORT_PASSWORD=' "$W/with space/env" && fail "PG_IMPORT_PASSWORD was left in the env file"
grep -q 'docker-compose.postgres-docstore.yml' "$W/with space/env" || fail "flip did not record the override"
grep -q '^COMPOSE_PROJECT_NAME=second$' "$W/with space/env" || fail "an existing line was lost"
# the real docker sees the project and the env file (checked on the raw call, not the normalised log)
cat > "$W/bin/docker2" <<STUB
#!/bin/sh
echo "\$*" >> "$W/raw.log"; exec "$W/bin/docker" "\$@"
STUB
chmod +x "$W/bin/docker2"; mkdir "$W/p"; cp "$W/bin/docker" "$W/p/docker.real"; cp "$W/bin/docker2" "$W/p/docker"; sed -i "s|exec \"$W/bin/docker\"|exec \"$W/p/docker.real\"|" "$W/p/docker"
reset; : > "$W/raw.log"; echo "COMPOSE_PROJECT_NAME=second" > "$W/with space/env"
(cd "$HERE/.." && PATH="$W/p:$PATH" PIPESHUB_ENV_FILE="$W/with space/env" scripts/migrate-to-postgres.sh --dry-run >/dev/null 2>&1) || fail "dry run with a spaced env path failed"
grep -q -- "--env-file $W/with space/env -p second -f docker-compose.yml" "$W/raw.log" || fail "the env file with a space or the project name did not reach docker"
# 4. rollback: login shut before up -d, override removed
reset; printf 'PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml\n' > "$W/env4"
run "$W/env4" scripts/rollback-to-mongo.sh || fail "rollback should exit 0"
off="$(pos 'ALTER ROLE pipeshub_import NOLOGIN')"; up="$(pos '^compose up -d$' tail)"
[ -n "$off" ] && [ -n "$up" ] && [ "$off" -lt "$up" ] || fail "rollback did not shut the import login before up -d ($off/$up)"
grep -q 'postgres-docstore' "$W/env4" && fail "rollback left the PostgreSQL override"
grep -q '^PG_IMPORT_PASSWORD=' "$W/env4" && fail "rollback left PG_IMPORT_PASSWORD in the env file"
# 5. no application container: refused before anything is changed
reset; touch "$W/noapp"; : > "$W/env5"
run "$W/env5" scripts/migrate-to-postgres.sh && fail "a project without an application container must be refused"
grep -q "No pipeshub-ai container" "$W/out" || fail "the refusal did not say why"
grep -q "up -d" "$W/calls.log" && fail "something was started before the refusal"
run "$W/env5" scripts/rollback-to-mongo.sh && fail "rollback must refuse a project without an application container"
# 6. SIGTERM during the drain ends the drain and exits non-zero
reset; touch "$W/slow"; : > "$W/env6"
(cd "$HERE/.." && PATH="$W/bin:$PATH" PIPESHUB_ENV_FILE="$W/env6" exec scripts/migrate-to-postgres.sh >"$W/out" 2>&1) & pid=$!
for _ in $(seq 1 50); do [ -f "$W/draining" ] && break; sleep 0.1; done; [ -f "$W/draining" ] || fail "the drain never started"
kill -TERM "$pid"; wait "$pid" && fail "a terminated move must exit non-zero"
grep -q "drain --cancel" "$W/calls.log" || fail "SIGTERM did not cancel the drain"
# 7. a login that cannot be closed is reported and its password kept
reset; touch "$W/failoff"; : > "$W/env7"
run "$W/env7" scripts/migrate-to-postgres.sh --dry-run && fail "an unclosable import login must fail the run"
grep -q "WARNING: could not close the PostgreSQL import login" "$W/out" || fail "no warning"
grep -q '^PG_IMPORT_PASSWORD=' "$W/env7" || fail "the password was dropped while the login may be open"
# 8. options: no value, a non-number, an unknown one
reset; : > "$W/env8"
for a in "--drain-wait" "--drain-wait abc" "--bogus"; do
  # shellcheck disable=SC2086
  run "$W/env8" scripts/migrate-to-postgres.sh $a && fail "'$a' must be a usage error"; grep -q "^usage:" "$W/out" || fail "'$a': no usage message"
done
grep -q "kafka-1" "$W/calls.log" && grep -E "stop .*kafka-1" "$W/calls.log" && fail "kafka-1 was stopped"
echo "OK"
