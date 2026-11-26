"""kg: the change log marks the rows of grant changes on records and record groups (grant_only), so a grant that leaves a node granted restamps that node alone; the writers
of stamps and fences and those that take work off the change log or the restamp queue refuse projection code below epoch 9, which compiled stamps from the graph's grants"""
import re
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0021"
down_revision = "kg_0020"
branch_labels = None
depends_on = None
# A contract, as kg_0019: code of an older release compiles a stamp from whether the graph grants each ancestor, while this code restamps the records below a node only when
# kg's copy of that flag flips. Older code could compile a record against a grant change still waiting in the log, which a grant-only projection would then never revisit.
min_code_epoch = 21

EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
OLD_FLOOR = 8
# Copies verified by epoch 8 code are kept: once the log is drained every stamp equals a compile from the graph, and kg's flags equal the graph's, which is what this code
# compiles from; a row logged before this revision has no grant_only, so it restamps as before. Downgraded, they are epoch 8 again.
RELABEL = "UPDATE kg.projection_state SET code_epoch = {to} WHERE code_epoch = {frm}"
# kg_0004's capture, which the downgrade puts back
CAPTURE_0004 = re.search(r"CREATE FUNCTION kg\.log_graph_change\(\).*?END \$fn\$;", (Path(__file__).parent / "0004_change_capture.sql").read_text(), re.S).group(0)


def _relabel(frm: int, to: int) -> None:
    op.execute("ALTER TABLE kg.projection_state NO FORCE ROW LEVEL SECURITY")
    op.execute(RELABEL.format(frm=frm, to=to))
    op.execute("ALTER TABLE kg.projection_state FORCE ROW LEVEL SECURITY")


def _mine() -> list[Fn]:
    names = {"kg." + n for n in EPOCH_WRITERS}
    fns = [f for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.qualified in names]
    assert len(fns) == len(names), "the manifest names every writer of this revision"
    return fns


def as_before(fn: Fn) -> Fn:
    """The writer as kg_0020 left it: the epoch floor at 8."""
    return replace(fn, args=tuple(replace(a, range=(OLD_FLOOR, a.range[1])) if a.name == "p_projector_epoch" else a for a in fn.args))


def _drop(fns: list[Fn]) -> None:
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0019 does).
    for fn in fns:
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")


def upgrade():
    run_sql(__file__)
    _drop(_mine())
    run_vetted(__file__, *EPOCH_WRITERS)
    _relabel(8, 9)                                       # last: the table's lock is held only to the commit


def downgrade():
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
    op.execute(CAPTURE_0004.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute("SELECT kernel.exempt_trigger('kg.log_graph_change()', 'inserts the keys of the changed nodes into kg.graph_changes as the writer of graph.*, in its transaction; "
               "reads nothing but its transition tables', 'security-review')")
    op.execute("ALTER TABLE kg.graph_changes DROP COLUMN grant_only")
    _relabel(9, 8)
