"""kg: the records writer takes the PARENT_AND_GRANT access rule (a record with it refused its whole batch, and with it the copy of its connector); the writers of stamps
and fences and those that take work off the change log or the restamp queue refuse projection code below epoch 7, which compiled such a record's access class without the rule"""
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0017"
down_revision = "kg_0016"
branch_labels = None
depends_on = None
# A contract, as kg_0014 is: code of an older release reads PARENT_AND_GRANT as an unknown rule, so its projector would stamp such a record (and what it holds) as readable by
# no one, at the epoch this code also writes, and the stamp would stand. The epoch floor refuses it, and the projection code epoch 7 has every connector copied and
# verified again before it is served.
min_code_epoch = 17

EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
RECORDS_WRITER = "w_records_upsert"
OLD_FLOOR = 6


def _mine() -> list[Fn]:
    names = {"kg." + n for n in (*EPOCH_WRITERS, RECORDS_WRITER)}
    fns = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.qualified in names]
    assert len(fns) == len(names), "the manifest names every writer of this revision"
    return fns


def as_before(fn: Fn) -> Fn:
    """The writer as kg_0016 left it: the epoch floor at 6."""
    return replace(fn, args=tuple(replace(a, range=(OLD_FLOOR, a.range[1])) if a.name == "p_projector_epoch" else a for a in fn.args))


def _drop(fns: list[Fn]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0014 does).
    for fn in fns:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")


def upgrade():
    _drop(_mine())
    run_vetted(__file__, *EPOCH_WRITERS, RECORDS_WRITER)


def downgrade():
    # The floor goes back to 6. The records writer keeps taking PARENT_AND_GRANT (kg_0002 owns it and runs the same generated file, as for kg_0010): older code never
    # writes that rule. Only the writers that are there: one dropped by hand stays dropped (a later step refuses what it left behind, as for any writer).
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if f.name in EPOCH_WRITERS and cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
