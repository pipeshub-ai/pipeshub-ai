"""graph: the partial indexes that serve a page of a code-graph hop in cursor order, so a node of any degree reads a page of its edges and not all of them"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0011"
down_revision = "graph_0010"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes only; older code reads the same rows through the indexes it used"

# (name, columns, predicate): the link edges of a node in the order of a hop page, byte-wise: the anchor (its collection, then its id), then the neighbour's collection and id. The rest of the page's order (the relationship type,
# the edge collection and the edge key) separates the few parallel edges of one neighbour, which a page sorts. The type is not an index column: it comes from the document, whose accessor a
# policy-bound index cannot use.
INDEXES = [
    ("edges_by_neighbour_in", 'org_id, to_coll, to_id COLLATE "C", from_coll COLLATE "C", from_id COLLATE "C"', "coll IN ('nodeRelations', 'recordLinks')"),
    ("edges_by_neighbour_out", 'org_id, from_coll, from_id COLLATE "C", to_coll COLLATE "C", to_id COLLATE "C"', "coll IN ('nodeRelations', 'recordLinks')"),
]


def upgrade():
    # Built inside the upgrade's one transaction, so the writers of a leaf wait for its build (as graph_0002 and graph_0006 do).
    # shortcut: a build reads a whole leaf, with writers blocked: run this upgrade on a graph that holds many edges in a maintenance window. No release holds a graph built before these revisions.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.edges", name, cols, where=where, blocking=True)


def downgrade():
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
