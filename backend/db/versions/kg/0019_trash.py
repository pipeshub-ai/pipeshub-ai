"""kg: the trash columns of the records (deleted_ms, delete_batch), their vetted writer and the indexes a trash listing pages by; the projection reads a node's
deletedAtTimestamp and deleteBatchId; the writers of stamps and fences and those that take work off the change log or the restamp queue refuse projection code below
epoch 8, which wrote no trash columns"""
import runpy
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0019"
down_revision = "kg_0018"
branch_labels = None
depends_on = None
# A contract, as kg_0014: code of an older release projects a delete without the trash columns and is refused by the epoch floor, so it cannot leave a trashed record
# out of the listing that pages by them. The copies it made are filled in place here (FILL) and keep being served.
min_code_epoch = 19

WRITERS = ("w_trash_set",)
EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
OLD_FLOOR = 7
# One connector's trashed records in the listing's order (a page is a range, read backwards), and one delete batch's trashed records (what a page row counts).
INDEXES = [
    ("records_trash", "org_id, app_sk, deleted_ms, delete_batch", "coll = 3 AND deleted AND deleted_ms IS NOT NULL AND delete_batch IS NOT NULL"),
    ("records_trash_batch", "org_id, delete_batch", "coll = 3 AND deleted AND delete_batch IS NOT NULL"),
]

# The copies made before this revision, filled in place so that no connector is copied again: each row's deleted_ms and delete_batch from its node's document,
# exactly as kg.model reads them (``_ms``: an integer as it is, a fraction or a numeric string as a double truncated, a boolean, text that is no number, inf or nan as
# nothing, nothing outside int64; ``_s``: ``str()`` of the JSON value), for what SQL can tell apart as Python does. A value it cannot (a string Python's float()
# might read and these patterns do not, a fractional number or a structure as a batch) is ``odd``: that row's connector is copied again rather than guessed.
# The document is read as kg.reader reads it, PostgreSQL's text of the JSON, so a number's integer-or-fraction is its jsonb text.
FILL = r"""WITH f AS MATERIALIZED (
SELECT k.org_id, k.sk, k.app_sk, m.ms, b.batch, h.how <> 'odd' AND b.ok AS ok
FROM graph.nodes n
JOIN kg.records k ON k.org_id = n.org_id AND k.coll = CASE n.coll WHEN 'apps' THEN 1 WHEN 'recordGroups' THEN 2 ELSE 3 END AND k.id = n.id
CROSS JOIN LATERAL (SELECT jsonb_typeof(n.doc->'deletedAtTimestamp') AS tt, n.doc->>'deletedAtTimestamp' AS ts,
                           jsonb_typeof(n.doc->'deleteBatchId') AS bt, n.doc->>'deleteBatchId' AS bs) a
CROSS JOIN LATERAL (SELECT CASE
    WHEN a.tt = 'number' AND a.ts ~ '^-?[0-9]+$' THEN 'int'
    WHEN a.tt = 'number' THEN 'float'
    WHEN a.tt = 'string' AND a.ts ~ '^[ \t\n\r\f\v]*[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?[ \t\n\r\f\v]*$' THEN 'float'
    WHEN a.tt = 'string' AND lower(btrim(a.ts, E' \t\n\r\f\v')) ~ '^[+-]?(inf|infinity|nan)$' THEN 'none'
    WHEN a.tt = 'string' AND a.ts !~ '[0-9]' AND a.ts !~ '[^\x01-\x7f]' THEN 'none'
    WHEN a.tt = 'string' THEN 'odd'
    ELSE 'none' END AS how) h
CROSS JOIN LATERAL (SELECT CASE h.how
    WHEN 'int' THEN CASE WHEN a.ts::numeric >= -9223372036854775808 AND a.ts::numeric < 9223372036854775808 THEN a.ts::numeric::bigint END
    WHEN 'float' THEN (SELECT CASE WHEN abs(x) < 1e-300 THEN 0 WHEN abs(x) >= 1e300 THEN NULL
                                   WHEN x::float8 >= -9223372036854775808::float8 AND x::float8 < 9223372036854775808::float8 THEN trunc(x::float8)::bigint END
                       FROM (SELECT btrim(a.ts, E' \t\n\r\f\v')::numeric AS x) q) END AS ms) m
CROSS JOIN LATERAL (SELECT CASE WHEN a.bt = 'boolean' THEN CASE a.bs WHEN 'true' THEN 'True' ELSE 'False' END WHEN a.bt IN ('string', 'number') THEN a.bs END AS batch,
                           a.bt IS NULL OR a.bt IN ('null', 'string', 'boolean') OR (a.bt = 'number' AND a.bs ~ '^-?[0-9]+$') AS ok) b
WHERE n.coll IN ('apps', 'recordGroups', 'records') AND (n.doc ? 'deletedAtTimestamp' OR n.doc ? 'deleteBatchId')),
  filled AS (UPDATE kg.records k SET deleted_ms = f.ms, delete_batch = f.batch FROM f WHERE f.ok AND k.org_id = f.org_id AND k.sk = f.sk RETURNING 1)
UPDATE kg.projection_state s SET mode = 'legacy', code_epoch = 0, backfill_cursor = NULL, verified_at = NULL, verify_digest = NULL
  FROM (SELECT DISTINCT org_id, app_sk FROM f WHERE NOT ok) o WHERE s.org_id = o.org_id AND s.app_sk = o.app_sk;
UPDATE kg.projection_state SET code_epoch = 8 WHERE code_epoch = 7;"""
# The tables force row-level security, which hides every org's rows from the owner: lifted for the fill (as retrieval_0002 does) and forced again in the same transaction.
FORCED = ("graph.nodes", "kg.records", "kg.projection_state")


def _fill() -> None:
    for t in FORCED:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
    # The counters' trigger would aggregate every filled row into no delta (the trash columns are no counter key): left out of the fill.
    op.execute("ALTER TABLE kg.records DISABLE TRIGGER kg_records_count_update")
    op.get_bind().connection.cursor().execute(FILL)
    op.execute("ALTER TABLE kg.records ENABLE TRIGGER kg_records_count_update")
    for t in FORCED:
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")


def _mine() -> list[Fn]:
    names = {"kg." + n for n in EPOCH_WRITERS}
    fns = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.qualified in names]
    assert len(fns) == len(names), "the manifest names every writer of this revision"
    return fns


def as_before(fn: Fn) -> Fn:
    """The writer as kg_0018 left it: the epoch floor at 7."""
    return replace(fn, args=tuple(replace(a, range=(OLD_FLOOR, a.range[1])) if a.name == "p_projector_epoch" else a for a in fn.args))


def _drop(fns: list[Fn]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0017 does).
    for fn in fns:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")


def upgrade():
    run_sql(__file__)
    _fill()
    # shortcut: built inside the upgrade's one transaction with the writers of kg.records waiting (as kg_0009): run this upgrade on an install whose typed graph is large in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "kg.records", name, cols, where=where, blocking=True)
    _drop(_mine())
    run_vetted(__file__, *WRITERS, *EPOCH_WRITERS)


def downgrade():
    # The floor goes back to 7 (only the writers that are there, as kg_0017), the trash writer goes as kg_0009's writer goes, then the projected subset of kg_0018, the
    # indexes and the columns.
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
    trash = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.name in WRITERS]
    _drop([f for f in trash if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]])
    op.execute("SELECT kernel._sync_vetted_privs('kg.records'::regclass)")
    op.execute(runpy.run_path(str(Path(__file__).with_name("0018_proj_doc_access_only.py")))["UPGRADE"])
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{name}")
    op.execute("ALTER TABLE kg.records DROP COLUMN deleted_ms, DROP COLUMN delete_batch")
    # A copy of this code is one of kg_0018's once the columns are gone: served again by the code of that release.
    op.execute("ALTER TABLE kg.projection_state NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE kg.projection_state SET code_epoch = 7 WHERE code_epoch = 8")
    op.execute("ALTER TABLE kg.projection_state FORCE ROW LEVEL SECURITY")
