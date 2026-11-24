"""kg: links of kind 3 name every PARENT_CHILD parent of a node (the primary one too), so a read that follows PARENT_CHILD relations only (a folder move's guards) can be
answered from kg; the writers of stamps and fences and those that take work off the change log or the restamp queue refuse projection code below epoch 6, which wrote none"""
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0014"
down_revision = "kg_0013"
branch_labels = None
depends_on = None
# A contract, as kg_0013: code of an older release projects without the kind-3 links and is refused by the epoch floor, so it cannot leave a node's PARENT_CHILD parents
# behind its other links while the move guards read them.
min_code_epoch = 14

EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
KIND_WRITERS = ("w_links_set", "w_links_delete")
OLD_FLOOR = 5


def _mine() -> list[Fn]:
    names = {"kg." + n for n in EPOCH_WRITERS + KIND_WRITERS}
    fns = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.qualified in names]
    assert len(fns) == len(names), "the manifest names every writer of this revision"
    return fns


def as_before(fn: Fn) -> Fn:
    """The writer as kg_0013 left it: the epoch floor at 5."""
    return replace(fn, args=tuple(replace(a, range=(OLD_FLOOR, a.range[1])) if a.name == "p_projector_epoch" else a for a in fn.args))


def _drop(fns: list[Fn]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0012 does).
    for fn in fns:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")


def upgrade():
    _drop(_mine())
    run_vetted(__file__, *EPOCH_WRITERS, *KIND_WRITERS)


def downgrade():
    # The floor goes back to 5. The links writers keep taking kind 3 (as kg_0012 kept its body): older code reads links by kind, never kind 3, and its projection
    # drops the kind-3 rows it finds as it copies each node again (they are not in what it derives), which needs its links writers to take them.
    # Only the writers that are there: one dropped by hand stays dropped (a later step refuses what it left behind, as for any writer).
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if f.name in EPOCH_WRITERS and cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
