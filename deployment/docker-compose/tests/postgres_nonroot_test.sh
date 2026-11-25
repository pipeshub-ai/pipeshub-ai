#!/usr/bin/env bash
# The server and the pooler under the restrictions the Helm chart puts on their pods: a non-root user, a read-only root filesystem,
# no capabilities, no privilege escalation, and writable space only where the pod mounts it (the data volume, the verifier directory,
# the socket directory and /tmp). A first start must create the cluster and the roles, write the verifier file, and the pooler must
# authenticate a login from it; a second start must keep working. Needs docker; PG_IMAGE_<major> names the built images (default
# pmcompose/postgres:<major>-pmc). The pooler's config names its server `postgres`; in the pod that name points at 127.0.0.1
# (hostAliases), here --add-host does the same. Test-only: PROJECT_PREFIX names the containers and volumes.
set -euo pipefail
DOCKER="${DOCKER:-docker}"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
names=(); vols=()
cleanup() { for n in "${names[@]}"; do $DOCKER rm -f "$n" >/dev/null 2>&1 || true; done; for v in "${vols[@]}"; do $DOCKER volume rm "$v" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT
RESTRICT=(--user 999:999 --read-only --cap-drop ALL --security-opt no-new-privileges)

for major in 17 18; do
  image_var="PG_IMAGE_$major"; image="${!image_var:-pmcompose/postgres:$major-pmc}"
  base="${PROJECT_PREFIX:-pgimg}-nonroot-$major-$$"
  data="$base-data"; auth="$base-auth"; vols+=("$data" "$auth"); names+=("$base-pg" "$base-pgb" "$base-chown")
  $DOCKER volume create "$data" >/dev/null; $DOCKER volume create "$auth" >/dev/null
  # what the kubelet does with fsGroup: hand the volumes to group 999
  $DOCKER run --rm --name "$base-chown" --user 0 --entrypoint sh -v "$data:/d" -v "$auth:/a" "$image" -c 'chown 999:999 /d /a && chmod 0770 /d /a'
  env=(-e POSTGRES_DB=pipeshub -e PGDATA=/var/lib/postgresql/data/pgdata)
  for v in POSTGRES_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_KV_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD; do
    printf -v "$v" '%s' "$(pw)"; env+=(-e "$v=${!v}")
  done
  start() {
    $DOCKER rm -f "$base-pg" "$base-pgb" >/dev/null 2>&1 || true
    $DOCKER run -d --name "$base-pg" "${RESTRICT[@]}" --add-host postgres:127.0.0.1 "${env[@]}" -v "$data:/var/lib/postgresql/data" -v "$auth:/pgbouncer-auth" \
      --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp:uid=999,gid=999 "$image" >/dev/null
    $DOCKER run -d --name "$base-pgb" "${RESTRICT[@]}" --network "container:$base-pg" -v "$auth:/pgbouncer-auth:ro" --entrypoint sh "$image" \
      -c 'until [ -s /pgbouncer-auth/userlist.txt ]; do sleep 1; done; exec pgbouncer /etc/pgbouncer/pgbouncer.ini' >/dev/null
    for _ in $(seq 1 90); do
      if $DOCKER exec "$base-pgb" pg_isready -q -h 127.0.0.1 -p 6432 >/dev/null 2>&1 \
        && $DOCKER exec "$base-pg" pg_isready -q -h 127.0.0.1 -U postgres -d pipeshub >/dev/null 2>&1; then return 0; fi
      sleep 2
    done
    echo "---- postgres log:"; $DOCKER logs --tail 20 "$base-pg" 2>&1; return 1
  }
  via() { $DOCKER exec -e PGPASSWORD="$2" "$base-pg" psql -h 127.0.0.1 -p 6432 -U "$1" -d pipeshub -tAc 'select current_user' 2>&1; }
  roles() { $DOCKER exec "$base-pg" psql -h 127.0.0.1 -U postgres -d pipeshub -tAc "select count(*) from pg_roles where rolname like 'pipeshub\\_%'"; }

  start && started=yes || started=no
  check "$major: first start under the pod restrictions reaches a working pooler" "$started" yes
  check "$major: the server runs as uid 999" "$($DOCKER exec "$base-pg" id -u)" 999
  check "$major: the roles were created by the non-root start" "$([ "$(roles)" -ge 20 ] && echo yes)" yes
  check "$major: the pooler authenticates the node login from the verifier file" "$(via pipeshub_node_login "$PG_NODE_PASSWORD")" pipeshub_node_login
  check "$major: the root filesystem is read-only" "$($DOCKER inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$base-pg")" true
  $DOCKER exec "$base-pg" psql -U postgres -d pipeshub -tAc "create table kept(v text); insert into kept values ('yes')" >/dev/null
  start && started=yes || started=no
  check "$major: a restart under the same restrictions works" "$started" yes
  check "$major: and keeps the data" "$($DOCKER exec "$base-pg" psql -U postgres -d pipeshub -tAc 'select v from kept')" yes
  $DOCKER rm -f "$base-pg" "$base-pgb" >/dev/null
done
exit $fail
