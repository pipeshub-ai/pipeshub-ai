"""The statistics of the kg tables the knowledge-hub browse reads, refreshed after a connector's first load into kg or its count after a restore.

The connectors service records a request (its vetted writer, one row per org in ``kg.analyze_requests``): it cannot run ANALYZE itself, since "to analyze a
table, one must ordinarily have the MAINTAIN privilege on the table" (https://www.postgresql.org/docs/17/sql-analyze.html, Notes) and no application role holds
MAINTAIN (kernel check 12). The maintenance worker runs ANALYZE as the owner role. Until the statistics know an org, the planner puts the org at one row: every
index on kg leads with org_id, so a read bounded by the org alone then costs the same as one bounded by the key, and a browse may read every kg row of the org.

A request is deleted once every table has statistics taken after it (by this job or by autovacuum). A table analyzed within ``EVERY_S`` is not analyzed again (a
burst of first loads coalesces into one ANALYZE per table), and the requests it would cover wait for the next pass. ANALYZE runs with SKIP_LOCKED: "if a relation
cannot be locked immediately without waiting, the relation is skipped" (same page), so a concurrent ANALYZE, VACUUM or index build is not waited for; the
skipped table is taken by a later pass.

A later request moves the time forward (a first seed asks while most of a connector is still to be copied; the promotion that ends the load asks again), so
statistics taken before the load ended never close it.

shortcut: a load that asks while an ANALYZE of a table is running, and commits after the ANALYZE took its sample, is counted as covered by it; autovacuum or the
next request picks its rows up. Store the asker's commit order (an xid8) instead if that window ever matters.
"""
import os

import psycopg

# The kg tables the browse statements read (postgres/kh_browse.py, kh_listing.py, kg/listing.py, kg/read.py, kg/counts.py).
TABLES = ("kg.records", "kg.links", "kg.node_keys", "kg.scope_classes", "kg.projection_state", "kg.counts")
# shortcut: 10 minutes between two ANALYZEs of one table is a guess (an ANALYZE of kg.records samples 30,000 rows, about a second at a million rows); measure under a
# burst of first loads before changing it. PG_KG_ANALYZE_EVERY_S overrides it (the tests set 0).
EVERY_S = 600
_LAST = "SELECT greatest(s.last_analyze, s.last_autoanalyze) FROM pg_stat_all_tables s WHERE s.relid = %s::regclass"


def every_s() -> int:
    raw = os.environ.get("PG_KG_ANALYZE_EVERY_S", "")
    if raw == "":
        return EVERY_S
    if not raw.isdigit():
        raise ValueError(f"PG_KG_ANALYZE_EVERY_S must be a whole number of seconds, got {raw!r}")
    return int(raw)


def close_covered(jobs: psycopg.Connection) -> int:
    """Delete every request that every table has statistics newer than. Returns how many it deleted."""
    return jobs.execute(
        "DELETE FROM kg.analyze_requests WHERE requested_at <= "
        "(SELECT min(coalesce(greatest(s.last_analyze, s.last_autoanalyze), '-infinity')) FROM pg_stat_all_tables s WHERE s.relid = ANY(%s::regclass[]))",
        (list(TABLES),)).rowcount


def due(jobs: psycopg.Connection) -> list[str]:
    """The tables to analyze now: those whose statistics are older than some open request (the newest) and were not taken in the last ``every_s()`` seconds. Against the
    oldest, a table analyzed after it would wait for that request to close, which a table skipped as locked could hold open, while a later org's request waited too."""
    newest = jobs.execute("SELECT max(requested_at) FROM kg.analyze_requests").fetchone()[0]
    if newest is None:
        return []
    out = []
    for t in TABLES:
        last, recent = jobs.execute(
            "SELECT greatest(s.last_analyze, s.last_autoanalyze), greatest(s.last_analyze, s.last_autoanalyze) > now() - make_interval(secs => %s) "
            "FROM pg_stat_all_tables s WHERE s.relid = %s::regclass", (every_s(), t)).fetchone()
        if last is None or (last < newest and not recent):
            out.append(t)
    return out


def analyze(owner: psycopg.Connection, tables: list[str]) -> list[str]:
    """ANALYZE each of ``tables`` (names from TABLES only) as the owner. Returns the tables it analyzed (a table another session holds a conflicting lock on is skipped)."""
    done = []
    for t in tables:
        if t not in TABLES:
            raise ValueError(f"{t} is not a kg browse table")
        before = owner.execute(_LAST, (t,)).fetchone()[0]
        owner.execute(f"ANALYZE (SKIP_LOCKED) {t}")
        if owner.execute(_LAST, (t,)).fetchone()[0] != before:
            done.append(t)
    return done
