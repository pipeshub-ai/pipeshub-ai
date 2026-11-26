"""graph: the code graph's first block indexes (by record, and by connector and name) go; graph_0018's seek indexes serve every read of them"""
import runpy
from pathlib import Path

from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0020"
down_revision = "graph_0019"
branch_labels = None
depends_on = None
# Not a contract: a dropped index changes no answer, and nothing of any release plans with these two, so a rollback to an earlier build keeps working.
# nodes_codefile_path stays: the prefix read of a path is written for it (under row security it cannot reach it either, but a vetted prefix read would).
older_code_compatible = ("drops two indexes nothing reads: the vetted block reads (graph_0003's functions, whose bodies graph_0018 replaced in the "
                         "database whatever code runs) seek the hash indexes, and every service role's own statements run under row security, where "
                         "doc->> is not leakproof and is never an index condition, so their plans read the org's leaf with or without them")

# Dropped by db.migrate after the upgrade commits: a partitioned index cannot be dropped CONCURRENTLY, and a DROP inside the upgrade would keep
# every reader of graph.nodes out until the commit.
ONLINE_DROPS = ["graph.nodes_block_record", "graph.nodes_block_name"]
# graph_0019's index, built in its revision's transaction (it predates this list there): listed here so that an upgrade with graph_0019 pending
# builds it ahead, concurrently, like every other index on graph.nodes; graph_0019's own call then finds it valid. graph_0019 owns it (downgrade).
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_record_refresh_intent", "cols": "org_id, (id COLLATE ucs_basic)",
     "where": "coll = 'records' AND doc->>'refreshIntentId' IS NOT NULL"},
]


def upgrade():
    pass


def downgrade():
    # graph_0001's own definitions, so the surface after a downgrade is graph_0019's
    baseline = runpy.run_path(str(Path(__file__).with_name("0001_baseline.py")))["INDEXES"]
    for name, cols, where in baseline:
        if f"graph.{name}" in ONLINE_DROPS:
            ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)
