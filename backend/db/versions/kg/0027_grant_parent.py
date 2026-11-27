"""kg: the node_parent mark of grants (kg.scope_members.node_parent), its vetted writer and the index a viewer's chain-top grants are read by, now with the mark; the writers of
stamps and fences and those that take work off the change log or the restamp queue refuse projection code below epoch 13, which wrote no node_parent"""
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0027"
down_revision = "kg_0026"
branch_labels = None
depends_on = None
# A contract, as kg_0026: code of an older release moves a record or adds a parent without the mark, and a mark it leaves set would name a parent the record no longer has.
min_code_epoch = 27
blocking_index_ok = "the index keys node_parent, which this revision adds, so it cannot be built before its transaction"

WRITERS = ("w_grant_parent_set",)
EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
OLD_FLOOR = 12
# A viewer's grants of one connector that may be chain-tops, with the node's record group and its one hierarchy parent (kg_0025's index, which lacked the parent).
INDEX = ("scope_members_tops", "NOT parent_held AND gtype <> '@uar'")
COLS, OLD_COLS = "org_id, principal_sk, app_sk, node_sk, node_group, node_parent", "org_id, principal_sk, app_sk, node_sk, node_group"

# A copy labelled epoch 13 or later cannot have its marks when this revision adds them (a downgrade dropped them and an upgrade brings them back empty): it is labelled epoch 12
# so that the bootstrap fills them in place (kg.backfill.fill_grant_parents) before it is served or verified. The downgrade does the same, so the code of kg_0026 serves it again.
RELABEL = "UPDATE kg.projection_state SET code_epoch = 12 WHERE code_epoch >= 13"


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
    """The writer as kg_0026 left it: the epoch floor at 12."""
    return replace(fn, args=tuple(replace(a, range=(OLD_FLOOR, a.range[1])) if a.name == "p_projector_epoch" else a for a in fn.args))


def _drop(fns: list[Fn]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0019 does).
    for fn in fns:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")


def upgrade():
    run_sql(__file__)
    # shortcut: built inside the upgrade's one transaction with the writers of kg.scope_members waiting (as kg_0025): run this upgrade on an install whose typed graph is large in a maintenance window.
    ensure_partitioned_index(op.get_bind(), "kg.scope_members", INDEX[0], COLS, where=INDEX[1], blocking=True)
    _drop(_mine())
    run_vetted(__file__, *WRITERS, *EPOCH_WRITERS)
    _relabel()                                          # last: the table's lock is held only to the commit


def downgrade():
    # The floor goes back to 12 (only the writers that are there, as kg_0026), then the writer goes, the index goes back to kg_0025's and the column goes.
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
    marks = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.name in WRITERS]
    _drop([f for f in marks if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]])
    op.execute("SELECT kernel._sync_vetted_privs('kg.scope_members'::regclass)")
    op.execute(f"DROP INDEX IF EXISTS kg.{INDEX[0]}")
    op.execute("ALTER TABLE kg.scope_members DROP COLUMN node_parent")
    ensure_partitioned_index(op.get_bind(), "kg.scope_members", INDEX[0], OLD_COLS, where=INDEX[1], blocking=True)
    _relabel()
