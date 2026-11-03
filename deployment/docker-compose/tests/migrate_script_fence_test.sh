#!/usr/bin/env bash
# scripts/migrate-to-postgres.sh must not touch the fences once the move has succeeded: a failing final `docker compose up -d`
# (a busy port, say) used to run its failure trap, `drain --cancel`, which un-retired MongoDB. Uses a stand-in `docker` that logs
# its calls; no containers are started.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin"
cat > "$W/bin/docker" <<STUB
#!/bin/sh
echo "\$*" >> "$W/calls.log"
case "\$*" in
  *"config --services"*) printf 'mongodb\npostgres\npgbouncer\npipeshub-migrate\ndocstore-migrate\npipeshub-ai\n' ;;
  "compose up -d") exit 1 ;;          # the final start of the application fails
esac
exit 0
STUB
chmod +x "$W/bin/docker"
cd "$HERE/.." && PATH="$W/bin:$PATH" PIPESHUB_ENV_FILE="$W/env" scripts/migrate-to-postgres.sh --flip >/dev/null 2>&1 && { echo "FAIL: the script should have failed at the final up -d" >&2; exit 1; }
grep -q "migrate --offline" "$W/calls.log" || { echo "FAIL: the move did not run" >&2; exit 1; }
after="$(sed -n '/migrate --offline/,$p' "$W/calls.log")"
if echo "$after" | grep -q "drain --cancel"; then echo "FAIL: a failure after the move touched the fence (drain --cancel)" >&2; exit 1; fi
grep -q '^DOC_STORE=postgres' "$W/env" || { echo "FAIL: .env was not switched" >&2; exit 1; }
# a failure BEFORE the move still ends the drain
: > "$W/calls.log"
sed -i 's/^  \*"config --services"\*)/  *"migrate --offline"*) exit 1 ;;\n  *"config --services"*)/' "$W/bin/docker"
cd "$HERE/.." && PATH="$W/bin:$PATH" PIPESHUB_ENV_FILE="$W/env2" scripts/migrate-to-postgres.sh >/dev/null 2>&1 && { echo "FAIL: should have failed" >&2; exit 1; }
grep -q "drain --cancel" "$W/calls.log" || { echo "FAIL: a failed move did not cancel the drain" >&2; exit 1; }
# the import login is shut (and its password gone from .env) BEFORE the final up -d: the migrate job's surface check fails while it can log in
: > "$W/calls.log"; : > "$W/env3"
sed -i -e '/migrate --offline/d' -e '/"compose up -d")/d' "$W/bin/docker"
cd "$HERE/.." && PATH="$W/bin:$PATH" PIPESHUB_ENV_FILE="$W/env3" scripts/migrate-to-postgres.sh --flip >/dev/null 2>&1 || { echo "FAIL: the move with a working up -d should exit 0" >&2; exit 1; }
off="$(grep -n 'ALTER ROLE pipeshub_import NOLOGIN' "$W/calls.log" | head -1 | cut -d: -f1)"; up="$(grep -n '^compose up -d$' "$W/calls.log" | tail -1 | cut -d: -f1)"
[ -n "$off" ] && [ -n "$up" ] && [ "$off" -lt "$up" ] || { echo "FAIL: the import login was not shut before the final up -d ($off/$up)" >&2; exit 1; }
if grep -q '^PG_IMPORT_PASSWORD=' "$W/env3"; then echo "FAIL: PG_IMPORT_PASSWORD was left in .env" >&2; exit 1; fi
: > "$W/calls.log"; : > "$W/env4"
cd "$HERE/.." && PATH="$W/bin:$PATH" PIPESHUB_ENV_FILE="$W/env4" scripts/rollback-to-mongo.sh >/dev/null 2>&1 || { echo "FAIL: rollback should exit 0" >&2; exit 1; }
off="$(grep -n 'ALTER ROLE pipeshub_import NOLOGIN' "$W/calls.log" | head -1 | cut -d: -f1)"; up="$(grep -n '^compose up -d$' "$W/calls.log" | tail -1 | cut -d: -f1)"
[ -n "$off" ] && [ -n "$up" ] && [ "$off" -lt "$up" ] || { echo "FAIL: rollback did not shut the import login before up -d ($off/$up)" >&2; exit 1; }
if grep -q '^PG_IMPORT_PASSWORD=' "$W/env4"; then echo "FAIL: rollback left PG_IMPORT_PASSWORD in .env" >&2; exit 1; fi
echo "OK"
