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
# out of the listing that pages by them.
min_code_epoch = 19

WRITERS = ("w_trash_set",)
EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
OLD_FLOOR = 7
# One connector's trashed records in the listing's order (a page is a range, read backwards), and one delete batch's trashed records (what a page row counts).
INDEXES = [
    ("records_trash", "org_id, app_sk, deleted_ms, delete_batch", "coll = 3 AND deleted AND deleted_ms IS NOT NULL AND delete_batch IS NOT NULL"),
    ("records_trash_batch", "org_id, delete_batch", "coll = 3 AND deleted AND delete_batch IS NOT NULL"),
]

# A copy labelled epoch 8 or later cannot have these columns filled when this revision adds them (a downgrade dropped them and an upgrade brings them back empty): it is
# labelled epoch 7 so that the bootstrap fills it in place (kg.backfill.fill_trash) before it is served or verified. The downgrade does the same, so the code of kg_0018
# serves again what it can read. A small table, and no graph table: its row security is lifted for this statement alone, as retrieval_0002 does.
RELABEL = "UPDATE kg.projection_state SET code_epoch = 7 WHERE code_epoch >= 8"


def _relabel() -> None:
    op.execute("ALTER TABLE kg.projection_state NO FORCE ROW LEVEL SECURITY")
    op.execute(RELABEL)
    op.execute("ALTER TABLE kg.projection_state FORCE ROW LEVEL SECURITY")


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
    # shortcut: built inside the upgrade's one transaction with the writers of kg.records waiting (as kg_0009): run this upgrade on an install whose typed graph is large in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "kg.records", name, cols, where=where, blocking=True)
    _drop(_mine())
    run_vetted(__file__, *WRITERS, *EPOCH_WRITERS)
    _relabel()                                          # last: the table's lock is held only to the commit


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
    _relabel()
