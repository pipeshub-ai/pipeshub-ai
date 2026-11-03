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
# a password outside printable ASCII is refused there too (the role script's SCRAM verifier needs it), and punctuation is fine
for v in POSTGRES_PASSWORD PG_NODE_PASSWORD PG_KV_PASSWORD; do
  EXTRA_ENV="$v=pässword" run "non-ASCII $v refused before anything is started" 78 ROOT/data
done
EXTRA_ENV='PG_NODE_PASSWORD=Ab1!@#%^&*()_+-=[]{}|:;<>,.?/~' run "punctuation in a password starts" 0 ROOT/data
exit $fail
