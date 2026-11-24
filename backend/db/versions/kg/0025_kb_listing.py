"""kg: what a knowledge base's folder listing and its trash's single-file view filter and order by (kg.records indexing_status, connector_name, doc_created_ms, doc_updated_ms,
file_size, file_kind, kb_odd) and their vetted writer; the capture logs a change of a record's indexing status or connector name, of its isOfType edges and of the file document they
point at; the writers of stamps and fences and those that take work off the change log or the restamp queue refuse projection code below epoch 11, which wrote none of them"""
import re
from dataclasses import replace
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import Fn, load, render, run_vetted

revision = "kg_0025"
down_revision = "kg_0024"
branch_labels = None
depends_on = None
# A contract, as kg_0024: code of an older release writes a record without these columns and acknowledges a status change without projecting it, so a listing read from
# them would keep a record under a status, a size or a kind it no longer has.
min_code_epoch = 25

WRITERS = ("w_kb_listing_set",)
EPOCH_WRITERS = ("w_stamps_set", "w_fence_rows", "w_queue_touch", "w_queue_done", "w_changes_ack", "w_changes_delete")
OLD_FLOOR = 10
COLUMNS = ("indexing_status", "connector_name", "doc_created_ms", "doc_updated_ms", "file_size", "file_kind", "kb_odd")

# A copy labelled epoch 11 or later cannot have these columns filled when this revision adds them (a downgrade dropped them and an upgrade brings them back empty): it is labelled
# epoch 10 so that the bootstrap fills it in place (kg.backfill.fill_kb_listing) before it is served or verified. The downgrade does the same, so the code of kg_0024 serves it again.
RELABEL = "UPDATE kg.projection_state SET code_epoch = 10 WHERE code_epoch >= 11"

_DIR = Path(__file__).parent
# What the downgrade puts back: kg_0021's capture, kg_0019's projected part of a node and kg_0003's ends of an edge.
CAPTURE_0021 = re.search(r"CREATE OR REPLACE FUNCTION kg\.log_graph_change\(\).*?END \$fn\$;", (_DIR / "0021_grant_only_changes.sql").read_text(), re.S).group(0)
PROJ_DOC_0019 = re.search(r"CREATE OR REPLACE FUNCTION kg\.proj_doc\(.*?\$\$;", (_DIR / "0019_trash.sql").read_text(), re.S).group(0)
EDGE_ENDS_0003 = re.search(r"CREATE FUNCTION kg\.edge_ends\(.*?\$\$;", (_DIR / "0003_projection_helpers.sql").read_text(), re.S).group(0).replace(
    "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def _relabel() -> None:
    op.execute("ALTER TABLE kg.projection_state NO FORCE ROW LEVEL SECURITY")
    op.execute(RELABEL)
    op.execute("ALTER TABLE kg.projection_state FORCE ROW LEVEL SECURITY")


def _mine() -> list[Fn]:
    names = {"kg." + n for n in EPOCH_WRITERS}
    fns = [f for f in load(_DIR / "vetted" / "manifest.yaml") if f.qualified in names]
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
    _drop(_mine())
    run_vetted(__file__, *WRITERS, *EPOCH_WRITERS)
    _relabel()                                          # last: the table's lock is held only to the commit


def downgrade():
    # The floor goes back to 10 (only the writers that are there, as kg_0019), then the writer, the capture, the helpers and the columns go.
    cur = op.get_bind().connection.cursor()
    fns = [f for f in _mine() if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]]
    _drop(fns)
    for fn in fns:
        cur.execute(render(as_before(fn)))
    listing = [f for f in load(_DIR / "vetted" / "manifest.yaml") if f.name in WRITERS]
    _drop([f for f in listing if cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f.signature,)).fetchone()[0]])
    op.execute("SELECT kernel._sync_vetted_privs('kg.records'::regclass)")
    op.execute(CAPTURE_0021)
    op.execute("SELECT kernel.exempt_trigger('kg.log_graph_change()', 'inserts the keys of the changed nodes into kg.graph_changes as the writer of graph.*, in its transaction; "
               "reads nothing but its transition tables', 'security-review')")
    op.execute(PROJ_DOC_0019)
    op.execute(EDGE_ENDS_0003)
    op.execute("ALTER TABLE kg.records " + ", ".join(f"DROP COLUMN {c}" for c in COLUMNS))
    _relabel()
