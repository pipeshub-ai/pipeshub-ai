"""kg: the placement marks of a grant (kg.scope_members.parent_held, node_group), their vetted writer and the index a viewer's chain-top grants are read by; the writers of
stamps and fences and those that take work off the change log or the restamp queue refuse projection code below epoch 11, which wrote no marks"""
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0025"
down_revision = "kg_0024"
branch_labels = None
depends_on = None
# A contract, as kg_0024: code of an older release moves a record or drops a grant without its marks, and a mark it leaves set would keep a chain-top off the browse.
min_code_epoch = 25
blocking_index_ok = "the index keys parent_held and node_group, which this revision adds, so it cannot be built before its transaction"

WRITERS = ("w_grant_marks_set",)
EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
OLD_FLOOR = 10
# A viewer's grants of one connector that may be chain-tops: the grants no parent the same principal holds covers, with the node's record group to leave out the open ones.
INDEXES = [("scope_members_tops", "org_id, principal_sk, app_sk, node_sk, node_group", "NOT parent_held AND gtype <> '@uar'")]

# A copy labelled epoch 11 or later cannot have its marks when this revision adds them (a downgrade dropped them and an upgrade brings them back at their defaults): it is labelled
# epoch 10 so that the bootstrap fills them in place (kg.backfill.fill_grant_marks) before it is served or verified. The downgrade does the same, so the code of kg_0024 serves it again.
RELABEL = "UPDATE kg.projection_state SET code_epoch = 10 WHERE code_epoch >= 11"


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
    """The writer as kg_0024 left it: the epoch floor at 10."""
    return replace(fn, args=tuple(replace(a, range=(OLD_FLOOR, a.range[1])) if a.name == "p_projector_epoch" else a for a in fn.args))


def _drop(fns: list[Fn]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0019 does).
    for fn in fns:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")


def upgrade():
    run_sql(__file__)
    # shortcut: built inside the upgrade's one transaction with the writers of kg.scope_members waiting (as kg_0024): run this upgrade on an install whose typed graph is large in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "kg.scope_members", name, cols, where=where, blocking=True)
    _drop(_mine())
    run_vetted(__file__, *WRITERS, *EPOCH_WRITERS)
    _relabel()                                          # last: the table's lock is held only to the commit


def downgrade():
    # The floor goes back to 10 (only the writers that are there, as kg_0024), then the writer, the index and the columns go.
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
    marks = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.name in WRITERS]
    _drop([f for f in marks if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]])
    op.execute("SELECT kernel._sync_vetted_privs('kg.scope_members'::regclass)")
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{name}")
    op.execute("ALTER TABLE kg.scope_members DROP COLUMN node_group")
    op.execute("ALTER TABLE kg.scope_members DROP COLUMN parent_held")
    _relabel()
