#!/usr/bin/env bash
# Restore drill for docker-compose.postgres-backup.yml, on PostgreSQL 17 and 18 (DRILL_MAJORS): seeds a table in every schema the
# application keeps state in, takes a full backup, changes data, marks time T, changes more (drops a table too), takes an incremental
# backup, changes more, then restores
#   - to T into a new volume (scripts/pg-restore.sh): row counts and checksums per schema must equal those read at T, and differ from now;
#   - to the end of the archive into a new volume: they must equal the live database's;
#   - to T in place, over the stack's own volume: equal to T again, and backups go on afterwards (pgbackrest check).
# It prints the measured backup and restore times and the archive lag (how much a restore can lose) for the RTO/RPO table in
# postgres/README.md. The repository is a new host directory on 17 (PG_BACKUP_REPO) and a new named volume on 18. Knobs: DRILL_MAJORS (17 18), DRILL_ROWS per schema (20000), DRILL_ARCHIVE_TIMEOUT (60 s), DRILL_PG_MEMORY (2G),
# DRILL_PG_SHARED_BUFFERS (256MB), DRILL_PGBACKREST_EXTRA (more PGBACKREST_* lines, for example another repository). PROJECT_PREFIX and
# EXTRA_COMPOSE_FILE as in the other tests here.
# Needs docker; PG_IMAGE_17 / PG_IMAGE_18 name the built images (default: pmcompose/postgres:17-pmc and :18-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
rows="${DRILL_ROWS:-20000}"
work="$(mktemp -d)"; proj=""; major=""
schemas="kernel platform identity oauth conversation content integrations graph kg retrieval"
copies=()
teardown() { # one iteration's copies, stack and host repository directory, on every way out of it
  for c in "${copies[@]}"; do $DOCKER rm -f "$c" >/dev/null 2>&1 || true; $DOCKER volume rm "$c" >/dev/null 2>&1 || true; done
  copies=()
  if [ -n "$proj" ]; then dc down -v >/dev/null 2>&1 || true; fi
  proj=""
  # the host repository directories belong to root and postgres (Docker created them)
  for d in "$work"/repo-*; do
    if [ -d "$d" ]; then $DOCKER run --rm -v "$work:/w" --entrypoint rm "$image" -rf "/w/${d##*/}" || true; fi
  done
}
cleanup() { teardown; rm -rf "$work"; }
trap cleanup EXIT
trap 'exit 1' INT TERM
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
differ() { if [ "$2" != "$3" ]; then echo "ok   $1"; else echo "FAIL $1: both are '$2'"; fail=1; fi; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD"

envfile() { # major image
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$1"; echo "PG_IMAGE=${2%:*}"; echo "PG_IMAGE_REV=${2##*:$1-}"
    echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=graph-neo4j,sandbox,postgres,postgres-migrate"; echo "PGBACKREST_ENV_FILE=$work/pgbackrest.env"
    echo "PG_MEMORY_LIMIT=${DRILL_PG_MEMORY:-2G}"; echo "PG_SHARED_BUFFERS=${DRILL_PG_SHARED_BUFFERS:-256MB}"
    echo "PG_ARCHIVE_TIMEOUT=${DRILL_ARCHIVE_TIMEOUT:-60}"; echo "PG_BACKUP_INCR_HOURS=10000"; echo "PG_BACKUP_FULL_DAYS=10000"
    echo "PG_BACKUP_RETRY_SECS=10"
    # 17: the repository in a host directory that does not exist yet, as PG_BACKUP_REPO gives it (Docker creates it, owned by root);
    # 18: the default named volume. Both are new, as on a first start.
    if [ "$1" = 17 ]; then echo "PG_BACKUP_REPO=$work/repo-$1"; fi; } > "$work/env$1"
  # the default retention: the full backups an outage triggers would expire the backup before it, but for the gap hold
  printf 'PGBACKREST_REPO1_RETENTION_FULL=2\nPGBACKREST_COMPRESS_TYPE=zst\nPGBACKREST_START_FAST=y\nPGBACKREST_ARCHIVE_PUSH_QUEUE_MAX=128MiB\n%s' \
    "${DRILL_PGBACKREST_EXTRA:-}" > "$work/pgbackrest.env"
}
# the stack exactly as postgres/README.md and the docs give it (document-store file and profiles included), through COMPOSE_FILE,
# COMPOSE_PROJECT_NAME and an env file holding COMPOSE_PROFILES; only postgres and pg-backup are started
stack_env() {
  export COMPOSE_FILE="docker-compose.yml:docker-compose.postgres-docstore.yml:docker-compose.postgres-backup.yml${EXTRA_COMPOSE_FILE:+:$EXTRA_COMPOSE_FILE}"
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
    -- the sum of a 64-bit hash of every row: independent of row order, and constant memory on large tables
    execute format('select count(*), coalesce(sum((''x'' || left(md5(x::text), 16))::bit(64)::bigint::numeric), 0)::text from %I.%I x',
                   t.schemaname, t.tablename)
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
  teardown   # the previous iteration's, also when it stopped early
  image_var="PG_IMAGE_$major"; default="pmcompose/postgres:$major-pmc"; image="${!image_var:-$default}"
  proj="${PROJECT_PREFIX:-pgimg}-backup-$major-$$"
  envfile "$major" "$image"; stack_env
  dc up -d --no-build postgres > "$work/up.log" 2>&1 || { echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; continue; }
  healthy postgres || { echo "FAIL $major: postgres did not become healthy"; dc logs postgres | tail -20; fail=1; continue; }
  check "$major: archiving is on" "$(sql -c 'show archive_mode')" on
  t0=$(now); seed; echo "info $major: seeded $rows rows per schema in $(secs "$t0" "$(now)")s, database $(sql -c "select pg_size_pretty(pg_database_size('pipeshub'))")"

  # the service's first run: stanza, check, then a full backup because there is none
  dc up -d --no-build pg-backup > "$work/up.log" 2>&1 || { echo "FAIL $major: pg-backup did not start:"; cat "$work/up.log"; fail=1; continue; }
  # pg-backup is healthy while its first full backup runs; wait for the backup itself
  for _ in $(seq 1 5400); do [ "$(backup_secs full 2>/dev/null)" != none ] && [ -n "$(backup_secs full 2>/dev/null)" ] && break; sleep 2; done
  healthy pg-backup || { echo "FAIL $major: pg-backup is not healthy after its first backup"; dc logs pg-backup | tail -30; fail=1; continue; }
  full_s=$(backup_secs full); echo "info $major: full backup took ${full_s}s"
  check "$major: the scheduler's first backup is a full one" "$([ "$full_s" != none ] && echo yes)" yes
  # the documented way to confirm protection
  check "$major: pgbackrest info shows status ok and a full backup" \
    "$(dc exec -T pg-backup pgbackrest info | grep -E 'status:|full backup:' | sed 's/^ *//; s/: [0-9-]*F$/: <label>/' | sort -u | tr '\n' ' ')" \
    "full backup: <label> status: ok "

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

  # A size raised on the server after the newest backup: the copy starts with the backup's value, and recovery pauses where the WAL
  # records the higher one. pg-restore.sh must stop and show why, not wait.
  sql -c "alter system set max_locks_per_transaction = 128" >/dev/null
  dc restart postgres >/dev/null 2>&1; healthy postgres || { echo "FAIL $major: postgres did not come back after the restart"; fail=1; }
  sql -c "create table platform.after_raise as select 1 as v" >/dev/null
  seg="$(sql -c 'select pg_walfile_name(pg_current_wal_lsn())')"; sql -c "select pg_switch_wal()" >/dev/null
  for _ in $(seq 1 60); do [ "$(sql -c "select coalesce(last_archived_wal >= '$seg', false) from pg_stat_archiver")" = t ] && break; sleep 2; done
  copy="${proj}_pg_restore_paused_$$"; copies+=("$copy")
  rc=0; PG_RESTORE_WAIT_SECS=900 timeout 1200 scripts/pg-restore.sh latest --into "$copy" > "$work/paused.log" 2>&1 || rc=$?
  check "$major: a paused recovery makes pg-restore.sh stop with an error" "$rc" 1
  check "$major: and show the log that names the setting" \
    "$(grep -q 'recovery paused before reaching the target' "$work/paused.log" && grep -q 'max_locks_per_transaction' "$work/paused.log" && echo yes)" yes

  check "$major: pg-restore.sh stops on Compose's error when the profiles do not match the stack's" \
    "$(COMPOSE_PROFILES=postgres scripts/pg-restore.sh latest >"$work/mismatch.log" 2>&1; echo "$? $(grep -c 'undefined service "pipeshub-migrate"' "$work/mismatch.log")")" "2 1"

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

  for _ in $(seq 1 75); do [ "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pg-backup-1")" = healthy ] && break; sleep 2; done
  check "$major: pg-backup is healthy after its restart, with no new backup" "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pg-backup-1")" healthy
  # The repository becomes unwritable: beyond the archive queue cap (128MiB here) the server drops WAL instead of filling its volume,
  # pg-backup turns unhealthy and says restores have a gap; once the repository is back, a full backup closes the gap.
  # before the outage: the newest backup, and every schema the outage does not write (it writes platform only)
  label_pre="$(dc exec -T pg-backup pgbackrest --output=json info | perl -MJSON::PP -e 'print decode_json(join "", <STDIN>)->[0]{backup}[-1]{label}')"
  untouched() { tr ' ' '\n' <<<"$1" | grep -v '^platform:' | tr '\n' ' '; }
  pre_outage="$(untouched "$(sql -c "$checksums")")"
  arch=/var/lib/pgbackrest/archive/pipeshub
  mode="$($DOCKER exec "$proj-pg-backup-1" stat -c %a "$arch")"
  # as its owner, postgres: pg-backup drops every capability, so root could not even reach the directory
  $DOCKER exec "$proj-pg-backup-1" chmod 000 "$arch"
  # many small transactions, so the dropped segments hold commits and the report can name the first lost one
  sql -c "create table platform.filler (g int, pad text)" \
      -c "do \$\$ begin for i in 1..250 loop insert into platform.filler select g, repeat(md5(g::text), 30) from generate_series(1, 1000) g; commit; end loop; end \$\$" \
      -c "select pg_switch_wal()" >/dev/null
  for _ in $(seq 1 90); do dc exec -T pg-backup test -f /tmp/pg-backup.ok || break; sleep 2; done
  check "$major: dropped WAL turns pg-backup unhealthy" "$(dc exec -T pg-backup test -f /tmp/pg-backup.ok && echo healthy || echo unhealthy)" unhealthy
  check "$major: the server stays up and writable" "$(sql -c 'insert into platform.filler values (0, $$x$$) returning g')" 0
  check "$major: pg-backup logs the dropped segments" "$(dc logs pg-backup 2>&1 | grep -c 'dropped WAL segments [0-9A-F]\{24\} to [0-9A-F]\{24\}' | awk '{print ($1 > 0)}')" 1
  lost="$(dc logs pg-backup 2>&1 | sed -n 's/.*its first transaction, committed at \([0-9-]* [0-9:.]*+00\), is the first one lost.*/\1/p' | head -1)"
  check "$major: and the first lost commit, read from the earliest dropped segment" "$([ -n "$lost" ] && echo yes)" yes
  for _ in $(seq 1 75); do [ "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pg-backup-1")" = unhealthy ] && break; sleep 2; done
  check "$major: docker reports pg-backup unhealthy" "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-pg-backup-1")" unhealthy
  $DOCKER exec "$proj-pg-backup-1" chmod "$mode" "$arch"
  for _ in $(seq 1 150); do dc exec -T pg-backup test -f /tmp/pg-backup.ok && break; sleep 2; done
  check "$major: a full backup closes the gap once the repository is back" \
    "$(dc exec -T pg-backup sh -c 'test -f /tmp/pg-backup.ok && [ -z "$(ls -A /var/run/postgresql/pgbackrest-wal-gap.d)" ]' && echo closed || echo open)" closed
  check "$major: the newest backup is a full one" "$(dc exec -T pg-backup pgbackrest --output=json info | perl -MJSON::PP -e '
    my @b = @{ decode_json(join "", <STDIN>)->[0]{backup} }; print $b[-1]{type}')" full
  # What the report promises. Recovery cannot reach the first lost commit (it ends at the gap, before the target, and the server stops:
  # "if a recovery target is configured but the archive recovery ends before the target is reached, the server will shut down with a
  # fatal error", PostgreSQL 19.5.6), while the end of the archive from the backup before the gap restores up to the gap.
  copy="${proj}_pg_restore_gap_$$"; copies+=("$copy"); rc=0
  [ -n "$lost" ] && { scripts/pg-restore.sh "$lost" --into "$copy" > "$work/gap-t.log" 2>&1 || rc=$?; }
  check "$major: a restore to the first lost commit fails, recovery ending at the gap" \
    "$rc $(grep -q 'recovery ended before configured recovery target was reached' "$work/gap-t.log" 2>/dev/null && echo ended)" "1 ended"
  # the outage's first gap (a drop during the gap-closing backup can open a second one, held with a later backup)
  check "$major: the backup before the outage is held against expiry" \
    "$(dc exec -T pg-backup sh -c 'cd /var/run/postgresql/pgbackrest-gap-hold.d && cat "$(ls | head -1)"')" "$label_pre"
  copy="${proj}_pg_restore_upto_gap_$$"; copies+=("$copy")
  if scripts/pg-restore.sh latest --set "$label_pre" --into "$copy" > "$work/gap-l.log" 2>&1; then
    check "$major: the end of the archive from the backup before the gap: the schemas the outage did not write are as before it" \
      "$(untouched "$(copy_sql "$copy" "$checksums")")" "$pre_outage"
    # the outage's table may not exist yet at the gap, when its creation went in the first dropped segment
    n_copy=0
    if [ "$(copy_sql "$copy" "select to_regclass('platform.filler') is not null")" = t ]; then n_copy="$(copy_sql "$copy" 'select count(*) from platform.filler')"; fi
    check "$major: and it stops at the gap: fewer outage rows than the live database" \
      "$(sql -c "select (select count(*) from platform.filler) > $n_copy")" t
  else
    echo "FAIL $major: pg-restore.sh latest --set $label_pre:"; tail -20 "$work/gap-l.log"; fail=1
  fi
  check "$major: clear-gap-hold removes the hold and expires by retention" \
    "$(dc exec -T pg-backup pg-backup.sh clear-gap-hold > "$work/clear.log" 2>&1 && dc exec -T pg-backup sh -c 'test -z "$(ls -A /var/run/postgresql/pgbackrest-gap-hold.d)"' && grep -q 'expire command end: completed successfully' "$work/clear.log" && echo ok)" ok
  # the healthcheck itself fails as soon as a drop is marked, even while the last backup's success is still recorded
  hc="$($DOCKER inspect -f '{{index .Config.Healthcheck.Test 1}}' "$proj-pg-backup-1")"
  check "$major: the healthcheck passes with no drop marked" "$($DOCKER exec "$proj-pg-backup-1" sh -c "$hc" && echo pass || echo fail)" pass
  $DOCKER exec "$proj-pg-backup-1" sh -c ': > /var/run/postgresql/pgbackrest-wal-gap.d/drill'
  check "$major: the healthcheck fails at once on a marked drop" "$($DOCKER exec "$proj-pg-backup-1" sh -c "$hc" && echo pass || echo fail)" fail
  $DOCKER exec "$proj-pg-backup-1" rm -f /var/run/postgresql/pgbackrest-wal-gap.d/drill

  # Losing the machine, as the docs describe it: everything but the repository is gone (here the repository is the local directory on
  # 17 and the named volume on 18, standing in for off-machine storage). The regular backup was taken with the application stopped,
  # after reading the WAL location and making a restore point and `pgbackrest check` (which archives it); with no stack started,
  # restore in place to that location, then start the stack: the data is that quiet moment's.
  change 4
  # the documented mark, through scripts/pipeshub-compose.sh, into the regular backup's directory; the restore below reads only that
  backup_dir="$work/regular-backup"; mkdir -p "$backup_dir"
  scripts/pipeshub-compose.sh exec -T postgres psql -U postgres -d pipeshub -Atc "select pg_current_wal_lsn()" > "$backup_dir/pg-restore-point.txt"
  scripts/pipeshub-compose.sh exec -T postgres psql -U postgres -d pipeshub -c "select pg_create_restore_point('regular-backup')" >/dev/null
  scripts/pipeshub-compose.sh exec -T pg-backup pgbackrest check > "$work/check.log" 2>&1 \
    || { echo "FAIL $major: pgbackrest check at the restore point"; tail -5 "$work/check.log"; fail=1; }
  at_loss="$(sql -c "$checksums")"
  # Why not a time: recovery to a time stops only at a later commit. A time an hour ahead is beyond every archived commit for certain
  # (whatever autovacuum or the archive timeout did since the mark), so recovery ends before it and the server shuts down.
  copy="${proj}_pg_restore_quiet_time_$$"; copies+=("$copy"); rc=0
  scripts/pg-restore.sh "$(sql -c "select clock_timestamp() + interval '1 hour'")" --into "$copy" > "$work/quiet-time.log" 2>&1 || rc=$?
  check "$major: a time after the last archived commit cannot be reached" \
    "$rc $(grep -q 'recovery ended before configured recovery target was reached' "$work/quiet-time.log" && echo ended)" "1 ended"
  # the restored copies run as one-off pg-backup containers, which mount the data volume too: they go first, as the docs say
  for c in "${copies[@]}"; do $DOCKER rm -f "$c" >/dev/null 2>&1 || true; $DOCKER volume rm "$c" >/dev/null 2>&1 || true; done; copies=()
  change 5
  seg="$(sql -c 'select pg_walfile_name(pg_current_wal_lsn())')"; sql -c "select pg_switch_wal()" >/dev/null
  for _ in $(seq 1 60); do [ "$(sql -c "select coalesce(last_archived_wal >= '$seg', false) from pg_stat_archiver")" = t ] && break; sleep 2; done
  dc down >/dev/null 2>&1
  for v in postgres_data pg_backup_socket pgbouncer_auth; do $DOCKER volume rm "${proj}_$v" >/dev/null || true; done
  check "$major: the lost machine has no database volume left" "$($DOCKER volume ls -q | grep -cx "${proj}_postgres_data")" 0
  t0=$(now)
  if scripts/pg-restore.sh "lsn:$(cat "$backup_dir/pg-restore-point.txt")" --in-place > "$work/loss.log" 2>&1; then
    dc up -d --no-build postgres > /dev/null 2>&1
    for _ in $(seq 1 600); do [ "$(sql -c 'select pg_is_in_recovery()' 2>/dev/null)" = f ] && break; sleep 1; done
    echo "info $major: restore after losing the machine, server accepting writes: $(secs "$t0" "$(now)")s"
    check "$major: after losing the machine: rows and checksums per schema equal those at the regular backup's restore point" \
      "$(sql -c "$checksums")" "$at_loss"
    dc up -d --no-build pg-backup > /dev/null 2>&1
    check "$major: and archiving and backups go on" \
      "$(dc exec -T pg-backup pgbackrest check > "$work/check.log" 2>&1 && echo ok || tail -5 "$work/check.log")" ok
  else
    echo "FAIL $major: restore after losing the machine:"; tail -20 "$work/loss.log"; fail=1
  fi
done

exit $fail
