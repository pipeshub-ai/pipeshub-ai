"""kg: whether a record has no PARENT_CHILD parent that is a record (kg.records.pc_top), its vetted writer and the index a knowledge base's root listing reads; the writers of
stamps and fences and those that take work off the change log or the restamp queue refuse projection code below epoch 10, which wrote no pc_top"""
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0024"
down_revision = "kg_0023"
branch_labels = None
depends_on = None
# A contract, as kg_0019: code of an older release writes a record without pc_top and is refused by the epoch floor, so it cannot leave a record that moved to a knowledge
# base's root (or out of it) listed where it was.
min_code_epoch = 24
blocking_index_ok = "the index keys pc_top, which this revision adds, so it cannot be built before its transaction"

WRITERS = ("w_pc_top_set",)
EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
OLD_FLOOR = 9
# One connector's live records with no PARENT_CHILD parent that is a record: the root of a knowledge base.
INDEXES = [("records_pc_tops", "org_id, app_sk, sk", "kind = 3 AND pc_top AND NOT deleted")]

# A copy labelled epoch 10 or later cannot have pc_top filled when this revision adds it (a downgrade dropped it and an upgrade brings it back false): it is labelled epoch 9 so
# that the bootstrap fills it in place (kg.backfill.fill_pc_tops) before it is served or verified. The downgrade does the same, so the code of kg_0023 serves it again.
RELABEL = "UPDATE kg.projection_state SET code_epoch = 9 WHERE code_epoch >= 10"


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
    """The writer as kg_0023 left it: the epoch floor at 9."""
    return replace(fn, args=tuple(replace(a, range=(OLD_FLOOR, a.range[1])) if a.name == "p_projector_epoch" else a for a in fn.args))


def _drop(fns: list[Fn]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0019 does).
    for fn in fns:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")


def upgrade():
    run_sql(__file__)
    # shortcut: built inside the upgrade's one transaction with the writers of kg.records waiting (as kg_0019): run this upgrade on an install whose typed graph is large in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "kg.records", name, cols, where=where, blocking=True)
    _drop(_mine())
    run_vetted(__file__, *WRITERS, *EPOCH_WRITERS)
    _relabel()                                          # last: the table's lock is held only to the commit


def downgrade():
    # The floor goes back to 9 (only the writers that are there, as kg_0019), then the writer, the index and the column go.
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
    tops = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.name in WRITERS]
    _drop([f for f in tops if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]])
    op.execute("SELECT kernel._sync_vetted_privs('kg.records'::regclass)")
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{name}")
    op.execute("ALTER TABLE kg.records DROP COLUMN pc_top")
    _relabel()
