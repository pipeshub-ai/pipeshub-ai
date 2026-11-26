#!/usr/bin/env bash
# postgres/pg-entrypoint.sh must refuse to start on a volume whose cluster is not where this container's PGDATA is, instead of
# initialising an empty cluster beside it. Runs the script with a stand-in docker-entrypoint.sh and a temporary volume root.
set -euo pipefail
cd "$(dirname "$0")/.."
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
printf '#!/bin/sh\necho STARTED "$@"\n' > "$work/bin/docker-entrypoint.sh"; chmod +x "$work/bin/docker-entrypoint.sh"
fail=0
pw="POSTGRES_PASSWORD=x PG_NODE_PASSWORD=x PG_CONNECTORS_PASSWORD=x PG_INDEXING_PASSWORD=x PG_QUERY_PASSWORD=x PG_JOBS_PASSWORD=x PG_MIGRATE_PASSWORD=x PG_BACKUP_PASSWORD=x PG_KV_PASSWORD=x"
run() { # name expected(0|78) pgdata, then the files (relative to the volume root) that hold a cluster
  local name="$1" want="$2" pgdata="$3"; shift 3
  rm -rf "$work/vol"; mkdir -p "$work/vol/data"
  for f in "$@"; do mkdir -p "$work/vol/$(dirname "$f")"; echo 17 > "$work/vol/$f"; done
  set +e
  out="$(env $pw ${EXTRA_ENV:-} PATH="$work/bin:$PATH" PG_ROLES_SCRIPT=/nonexistent PG_VOLUME_ROOT="$work/vol" PGDATA="${pgdata/#ROOT/$work/vol}" sh postgres/pg-entrypoint.sh postgres 2>&1)"; code=$?
  set -e
  ok=0
  if [ "$want" = 0 ]; then [ $code -eq 0 ] && [[ "$out" == *STARTED* ]] && ok=1; fi
  if [ "$want" = 78 ]; then [ $code -eq 78 ] && [[ "$out" != *STARTED* ]] && ok=1; fi
  if [ $ok = 1 ]; then echo "ok   $name"; else echo "FAIL $name (exit $code): $out"; fail=1; fi
}
run "empty volume starts"                              0  ROOT/data
run "17 cluster in its own data/ starts"               0  ROOT/data data/PG_VERSION
run "old layout (cluster at the volume root) refused"  78 ROOT/data PG_VERSION
run "17 cluster under a 18 PGDATA refused"             78 ROOT/18/docker data/PG_VERSION
run "18 cluster under a 17 PGDATA refused"             78 ROOT/data 18/docker/PG_VERSION
run "18 cluster in its own place starts"               0  ROOT/18/docker 18/docker/PG_VERSION
# the Helm chart's layout: PGDATA one level below the mount, because a subPath directory is created by the kubelet and is not ours
run "empty volume starts with PGDATA in data/pgdata"            0  ROOT/data/pgdata
run "cluster in data/pgdata starts with PGDATA there"           0  ROOT/data/pgdata data/pgdata/PG_VERSION
run "cluster in data/pgdata refused under the compose PGDATA"   78 ROOT/data data/pgdata/PG_VERSION
run "cluster at data/ refused under PGDATA=data/pgdata"         78 ROOT/data/pgdata data/PG_VERSION
# an empty password is refused before the stock entrypoint (which would create the cluster) is reached
for v in POSTGRES_PASSWORD PG_NODE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD; do
  EXTRA_ENV="$v=" run "empty $v refused before anything is started" 78 ROOT/data
done
# one rule for every password: letters, digits and . _ ~ - only (they go into URLs); everything else is refused before initdb
for v in POSTGRES_PASSWORD PG_NODE_PASSWORD PG_KV_PASSWORD; do
  EXTRA_ENV="$v=pässword" run "non-ASCII $v refused before anything is started" 78 ROOT/data
done
for bad in 'a@b' 'a:b' 'a/b' 'a?b' 'a#b' 'a%b' 'a!b' 'a=b'; do
  EXTRA_ENV="PG_JOBS_PASSWORD=$bad" run "password '$bad' refused" 78 ROOT/data
done
EXTRA_ENV='PG_NODE_PASSWORD=Ab1._~-' run "the allowed punctuation starts" 0 ROOT/data
# WAL archiving follows docker-compose.postgres-backup.yml (PGBACKREST_STANZA), and is off without it
archive="-c archive_mode=on -c archive_command=pgbackrest-archive-push.sh %p -c archive_timeout=60"
run "starts without a stanza" 0 ROOT/data
if [[ "$out" == *archive_* ]]; then echo "FAIL archiving is off without a stanza: $out"; fail=1; else echo "ok   archiving is off without a stanza"; fi
EXTRA_ENV='PGBACKREST_STANZA=pipeshub' run "starts with a stanza" 0 ROOT/data
if [[ "$out" == *"$archive"* ]]; then echo "ok   a stanza turns archiving on"; else echo "FAIL a stanza turns archiving on: $out"; fail=1; fi
# Role setup writes, so after a point-in-time restore it waits for the end of recovery (the server answers on TCP while it replays WAL,
# read-only), and only so long. Stand-ins: the server answers at once, is in recovery for its first three queries, the role script logs.
printf '#!/bin/sh\nexit 0\n' > "$work/bin/pg_isready"
# run as root, the entrypoint goes through gosu postgres
printf '#!/bin/sh\nshift\nexec "$@"\n' > "$work/bin/gosu"
# shellcheck disable=SC2016 # the stand-ins expand at their own run
printf '#!/bin/sh\nn=$(($(cat "%s/q" 2>/dev/null || echo 0) + 1)); echo $n > "%s/q"; [ "$n" -le "${RECOVERY_QUERIES:-3}" ] && echo t || echo f\n' \
  "$work" "$work" > "$work/bin/psql"
# shellcheck disable=SC2016
printf '#!/bin/sh\necho "ROLES after $(cat "%s/q") queries"\n' "$work" > "$work/roles.sh"
chmod +x "$work/bin/pg_isready" "$work/bin/gosu" "$work/bin/psql" "$work/roles.sh"
roles_run() { rm -f "$work/q"; rm -rf "$work/vol"; mkdir -p "$work/vol/data"
  # the role setup runs in the background with this output: $(...) returns once it is done
  # shellcheck disable=SC2086 # $pw and EXTRA_ENV are NAME=value words
  out="$(env $pw ${EXTRA_ENV:-} PATH="$work/bin:$PATH" PG_ROLES_SCRIPT="$work/roles.sh" PG_AUTH_DIR="$work/auth" PG_VOLUME_ROOT="$work/vol" \
         PGDATA="$work/vol/data" timeout 60 sh postgres/pg-entrypoint.sh postgres 2>&1)"; }
roles_run
if [[ "$out" == *"ROLES after 4 queries"* && "$out" == *"role setup waits until it ends"* && "$out" != *"trying role setup anyway"* ]]; then
  echo "ok   role setup waits for the end of recovery"; else echo "FAIL role setup waits for the end of recovery: $out"; fail=1; fi
EXTRA_ENV="RECOVERY_QUERIES=1000 PG_ROLES_RECOVERY_WAIT_SECS=2" roles_run
if [[ "$out" == *"still in recovery after 2s"* && "$out" == *"ROLES after"* ]]; then
  echo "ok   and only for PG_ROLES_RECOVERY_WAIT_SECS"; else echo "FAIL and only for PG_ROLES_RECOVERY_WAIT_SECS: $out"; fail=1; fi
exit $fail
