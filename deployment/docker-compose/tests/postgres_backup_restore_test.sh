#!/usr/bin/env bash
# Restore drill for docker-compose.postgres-backup.yml, on PostgreSQL 17 and 18 (DRILL_MAJORS): seeds a table in every schema the
# application keeps state in, takes a full backup, changes data, marks time T, changes more (drops a table too), takes an incremental
# backup, changes more, then restores
#   - to T into a new volume (scripts/pg-restore.sh): row counts and checksums per schema must equal those read at T, and differ from now;
#   - to the end of the archive into a new volume: they must equal the live database's;
#   - to T in place, over the stack's own volume: equal to T again, and backups go on afterwards (pgbackrest check).
# It prints the measured backup and restore times and the archive lag (how much a restore can lose) for the RTO/RPO table in
# postgres/README.md. DRILL_ROWS rows per schema (default 20000). PROJECT_PREFIX and EXTRA_COMPOSE_FILE as in the other tests here.
# Needs docker; PG_IMAGE_17 / PG_IMAGE_18 name the built images (default: pmcompose/postgres:17-pmc and :18-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
rows="${DRILL_ROWS:-20000}"
work="$(mktemp -d)"; proj=""; major=""
schemas="kernel platform identity oauth conversation content integrations graph kg retrieval"
copies=()
cleanup() {
  for c in "${copies[@]}"; do $DOCKER rm -f "$c" >/dev/null 2>&1 || true; $DOCKER volume rm "$c" >/dev/null 2>&1 || true; done
  if [ -n "$proj" ]; then dc down -v >/dev/null 2>&1 || true; fi
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 1' INT TERM
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
differ() { if [ "$2" != "$3" ]; then echo "ok   $1"; else echo "FAIL $1: both are '$2'"; fail=1; fi; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD"

envfile() { # major image
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$1"; echo "PG_IMAGE=${2%:*}"; echo "PG_IMAGE_REV=${2##*:$1-}"
    echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=postgres"; echo "PGBACKREST_ENV_FILE=$work/pgbackrest.env"
    echo "PG_MEMORY_LIMIT=${DRILL_PG_MEMORY:-2G}"; echo "PG_SHARED_BUFFERS=${DRILL_PG_SHARED_BUFFERS:-256MB}"
    echo "PG_ARCHIVE_TIMEOUT=${DRILL_ARCHIVE_TIMEOUT:-60}"; echo "PG_BACKUP_INCR_HOURS=10000"; echo "PG_BACKUP_FULL_DAYS=10000"; } > "$work/env$1"
  printf 'PGBACKREST_REPO1_RETENTION_FULL=2\nPGBACKREST_COMPRESS_TYPE=zst\nPGBACKREST_START_FAST=y\n%s' "${DRILL_PGBACKREST_EXTRA:-}" > "$work/pgbackrest.env"
}
# scripts/pg-restore.sh reads the stack the way an operator gives it: COMPOSE_FILE, COMPOSE_PROJECT_NAME and an env file
stack_env() {
  export COMPOSE_FILE="docker-compose.yml:docker-compose.postgres-backup.yml${EXTRA_COMPOSE_FILE:+:$EXTRA_COMPOSE_FILE}"
  export COMPOSE_PROJECT_NAME="$proj" COMPOSE_ENV_FILES="$work/env$major"
}
dc() { $DOCKER compose "$@"; }
healthy() { for _ in $(seq 1 "${2:-150}"); do [ "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-$1-1" 2>/dev/null)" = healthy ] && return 0; sleep 2; done; return 1; }
sql() { $DOCKER exec -i "$proj-postgres-1" psql -X -v ON_ERROR_STOP=1 -U postgres -d pipeshub -tAq "$@"; }
copy_sql() { $DOCKER exec -i "$1" psql -X -v ON_ERROR_STOP=1 -h /tmp -U postgres -d pipeshub -tAq -c "$2"; }
# one line per schema that holds tables: schema, tables, rows, checksum of every row of every table in it
checksums="select string_agg(format('%s:%s:%s:%s', s, n, r, h), ' ' order by s) from drill.checksums()"
now() { date +%s.%N; }
secs() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.1f", b-a}'; }
backup_secs() { # type -> duration pgBackRest recorded for the newest backup of that type
  dc exec -T pg-backup pgbackrest --output=json info | perl -MJSON::PP -e '
    my ($t) = @ARGV; my $d;
    for my $s (@{ decode_json(join "", <STDIN>) }) { for my $b (@{ $s->{backup} }) {
      $d = $b->{timestamp}{stop} - $b->{timestamp}{start} if $b->{type} eq $t } }
    print $d // "none";' "$1"
}

seed() {
  sql <<SQL
create schema drill;
create function drill.checksums() returns table(s text, n int, r bigint, h text) language plpgsql as \$\$
declare t record; c bigint; m text; begin
  create temp table if not exists acc(s text, r bigint, h text) on commit drop;
  for t in select schemaname, tablename from pg_tables where schemaname = any('{$(echo $schemas | tr ' ' ,)}') order by 1, 2 loop
    execute format('select count(*), md5(coalesce(string_agg(x::text, E''\n'' order by x::text), '''')) from %I.%I x', t.schemaname, t.tablename)
      into c, m;
    insert into acc values (t.schemaname, c, m);
  end loop;
  return query select acc.s, count(*)::int, sum(acc.r)::bigint, md5(string_agg(acc.h, ',' order by acc.h)) from acc group by acc.s;
end \$\$;
SQL
  for s in $schemas; do
    sql <<SQL
create schema if not exists $s;
create table $s.drill (id bigint primary key, org_id uuid not null, body text not null, doc jsonb not null,
                       $( [ "$s" = retrieval ] && echo "embedding ext.vector(64) not null," ) updated_at timestamptz not null default now());
insert into $s.drill (id, org_id, body, doc $( [ "$s" = retrieval ] && echo ", embedding" ))
  select g, md5('org' || g % 7)::uuid, repeat(md5(g::text), 6), jsonb_build_object('n', g, 'tags', array[g % 13, g % 17])
         $( [ "$s" = retrieval ] && echo ", (select array_agg(sin(g + k))::ext.vector(64) from generate_series(1, 64) k)" )
  from generate_series(1, $rows) g;
SQL
  done
  sql -c "create index on retrieval.drill using hnsw (embedding ext.vector_l2_ops)"
  sql -c "create table graph.edges as select g as src, g % $rows + 1 as dst, 'PARENT_CHILD'::text as kind from generate_series(1, $rows) g"
}
change() { # salt: updates, deletes and inserts in every drill table still there
  for s in $(sql -c "select schemaname from pg_tables where tablename = 'drill' order by 1"); do
    sql -c "update $s.drill set body = md5(body || '$1'), updated_at = now() where id % 10 = $(( $1 % 10 ))" \
        -c "delete from $s.drill where id % 20 = $(( ($1 + 3) % 20 ))" \
        -c "insert into $s.drill (id, org_id, body, doc $( [ "$s" = retrieval ] && echo ", embedding" )) select $rows * $1 + g, md5('org' || g)::uuid, md5(g::text), '{}' $( [ "$s" = retrieval ] && echo ", array_fill(0.5, array[64])::ext.vector(64)" ) from generate_series(1, $(( rows / 10 ))) g"
  done
}
restore_new() { # label target -> sets copy (the restored copy's container and volume) and took (seconds until it accepts writes)
  local t0
  copy="${proj}_pg_restore_$1_$$"; copies+=("$copy"); t0=$(now)
  scripts/pg-restore.sh "$2" --into "$copy" > "$work/restore.log" 2>&1 || { echo "FAIL $major: pg-restore.sh $2:"; tail -20 "$work/restore.log"; fail=1; return 1; }
  took=$(secs "$t0" "$(now)")
}

for major in ${DRILL_MAJORS:-17 18}; do
  image_var="PG_IMAGE_$major"; default="pmcompose/postgres:$major-pmc"; image="${!image_var:-$default}"
  proj="${PROJECT_PREFIX:-pgimg}-backup-$major-$$"
  envfile "$major" "$image"; stack_env
  dc up -d --no-build postgres > "$work/up.log" 2>&1 || { echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; continue; }
  healthy postgres || { echo "FAIL $major: postgres did not become healthy"; dc logs postgres | tail -20; fail=1; continue; }
  check "$major: archiving is on" "$(sql -c 'show archive_mode')" on
  t0=$(now); seed; echo "info $major: seeded $rows rows per schema in $(secs "$t0" "$(now)")s, database $(sql -c "select pg_size_pretty(pg_database_size('pipeshub'))")"

  # the service's first run: stanza, check, then a full backup because there is none
  dc up -d --no-build pg-backup > "$work/up.log" 2>&1 || { echo "FAIL $major: pg-backup did not start:"; cat "$work/up.log"; fail=1; continue; }
  healthy pg-backup 5400 || { echo "FAIL $major: the first backup did not succeed"; dc logs pg-backup | tail -30; fail=1; continue; }
  full_s=$(backup_secs full); echo "info $major: full backup took ${full_s}s"
  check "$major: the scheduler's first backup is a full one" "$([ "$full_s" != none ] && echo yes)" yes

  change 1
  at_t="$(sql -c "$checksums")"
  target="$(sql -c "select clock_timestamp()")"
  sleep 2
  change 2; sql -c "drop table kg.drill"
  t0=$(now); dc exec -T pg-backup pgbackrest backup --type=incr > "$work/incr.log" 2>&1 || { echo "FAIL $major: incremental backup:"; tail "$work/incr.log"; fail=1; }
  echo "info $major: incremental backup took $(secs "$t0" "$(now)")s (pgBackRest: $(backup_secs incr)s)"
  change 3

  # archive lag: a commit is safe once its WAL segment is archived; archive_timeout closes the segment without any help
  sql -c "create table platform.rpo_marker as select 1 as v"
  seg="$(sql -c 'select pg_walfile_name(pg_current_wal_lsn())')"; t0=$(now)
  until [ "$(sql -c "select coalesce(last_archived_wal >= '$seg', false) from pg_stat_archiver")" = t ]; do
    if [ "$(secs "$t0" "$(now)" | cut -d. -f1)" -gt $(( ${DRILL_ARCHIVE_TIMEOUT:-60} * 3 )) ]; then echo "FAIL $major: WAL not archived"; fail=1; break; fi
    sleep 1
  done
  echo "info $major: a commit reached the archive after $(secs "$t0" "$(now)")s (archive_timeout ${DRILL_ARCHIVE_TIMEOUT:-60}s)"
  echo "info $major: repository: $(dc exec -T pg-backup sh -c 'du -sh /var/lib/pgbackrest/backup /var/lib/pgbackrest/archive; find /var/lib/pgbackrest/archive -name "0000*" | wc -l' | tr '\n' ' ')(backups, archive, archived segments)"
  live="$(sql -c "$checksums")"
  differ "$major: the drill changed the data after T" "$live" "$at_t"

  if restore_new t "$target"; then
    echo "info $major: restore to T into a new volume, running: ${took}s"
    check "$major: restore to T: rows and checksums per schema equal those at T" "$(copy_sql "$copy" "$checksums")" "$at_t"
    check "$major: the copy has the server's preload list" "$(copy_sql "$copy" 'show shared_preload_libraries')" "$(sql -c 'show shared_preload_libraries')"
    check "$major: the copy does not archive" "$(copy_sql "$copy" 'show archive_mode')" off
  fi
  if restore_new latest latest; then
    echo "info $major: restore to the end of the archive into a new volume, running: ${took}s"
    check "$major: restore latest: rows and checksums per schema equal the live database" "$(copy_sql "$copy" "$checksums")" "$live"
  fi

  # in place, as after a mistake on the live database: stop, restore over the volume, start
  dc stop > /dev/null 2>&1
  check "$major: in-place restore is refused while the server runs" "$(dc start postgres >/dev/null 2>&1; scripts/pg-restore.sh "$target" --in-place >/dev/null 2>&1; echo $?)" 3
  dc stop > /dev/null 2>&1
  t0=$(now)
  scripts/pg-restore.sh "$target" --in-place > "$work/restore.log" 2>&1 || { echo "FAIL $major: in-place restore:"; tail -20 "$work/restore.log"; fail=1; }
  dc up -d --no-build postgres > /dev/null 2>&1
  for _ in $(seq 1 600); do [ "$(sql -c 'select pg_is_in_recovery()' 2>/dev/null)" = f ] && break; sleep 1; done
  echo "info $major: in-place restore to T, server accepting writes: $(secs "$t0" "$(now)")s"
  check "$major: in-place restore to T: rows and checksums per schema equal those at T" "$(sql -c "$checksums")" "$at_t"
  dc up -d --no-build pg-backup > /dev/null 2>&1
  check "$major: archiving and backups go on after an in-place restore" \
    "$(dc exec -T pg-backup pgbackrest check > "$work/check.log" 2>&1 && dc exec -T pg-backup pgbackrest backup --type=incr >> "$work/check.log" 2>&1 && echo ok || tail -5 "$work/check.log")" ok

  for c in "${copies[@]}"; do $DOCKER rm -f "$c" >/dev/null 2>&1; $DOCKER volume rm "$c" >/dev/null 2>&1; done; copies=()
  dc down -v >/dev/null 2>&1; proj=""
done

exit $fail
