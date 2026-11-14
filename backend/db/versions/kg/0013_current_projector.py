"""kg: the writers of stamps and fences, and those that take work off the change log or the restamp queue, take the caller's projection code epoch"""
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0013"
down_revision = "kg_0012"
branch_labels = None
depends_on = None
# A contract: code built before this revision calls these writers without the epoch and finds no such function, so it can neither write stamps nor take
# work off the change log or the restamp queue. Code older than kg_0008 projected without the access lists (kg.scopes) and r13, and could leave them
# older than the stamps and the change log it acknowledged.
min_code_epoch = 13

WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
EPOCH_ARG = "p_projector_epoch"


def _mine() -> list[Fn]:
    fns = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.qualified in {"kg." + n for n in WRITERS}]
    assert len(fns) == len(WRITERS) and all(f.args[-1].name == EPOCH_ARG for f in fns), "the manifest names every writer of this revision, the epoch last"
    return fns


def without_epoch(fn: Fn) -> Fn:
    """The writer as kg_0012 had it: the same body, no epoch argument (the body never reads it)."""
    return replace(fn, args=fn.args[:-1])


def _drop(sigs: list[str]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0012 does). A signature that
    # is not there is skipped: kg_0002 runs the generated files of today, so a database created after this revision has only the new writers.
    for sig in sigs:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{sig}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{sig}')")
        op.execute(f"DROP FUNCTION IF EXISTS {sig}")


def upgrade():
    fns = _mine()
    _drop([without_epoch(f).signature for f in fns] + [f.signature for f in fns])
    run_vetted(__file__, *WRITERS)


def downgrade():
    fns = _mine()
    _drop([f.signature for f in fns])
    for fn in fns:
        op.get_bind().connection.cursor().execute(render(without_epoch(fn)))
