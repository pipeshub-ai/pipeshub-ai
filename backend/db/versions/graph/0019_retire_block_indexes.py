"""graph: the code graph's first block indexes (by record, and by connector and name) go; graph_0018's seek indexes serve every read of them"""
import runpy
from pathlib import Path

from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0019"
down_revision = "graph_0018"
branch_labels = None
depends_on = None
# A contract, as every index drop is. Nothing reads these two since graph_0018: its vetted reads seek the hash indexes, and a service role's own
# statements cannot use an index keyed by doc->> under row security (->> is not leakproof, so it is never an index condition there). Code built
# from graph_0018 on runs against this schema. nodes_codefile_path stays: the prefix read of a path is written for it (under row security it
# cannot reach it either, but a vetted prefix read would).
min_code_epoch = 18

# Dropped by db.migrate after the upgrade commits: a partitioned index cannot be dropped CONCURRENTLY, and a DROP inside the upgrade would keep
# every reader of graph.nodes out until the commit.
ONLINE_DROPS = ["graph.nodes_block_record", "graph.nodes_block_name"]


def upgrade():
    pass


def downgrade():
    # graph_0001's own definitions, so the surface after a downgrade is graph_0018's
    baseline = runpy.run_path(str(Path(__file__).with_name("0001_baseline.py")))["INDEXES"]
    for name, cols, where in baseline:
        if f"graph.{name}" in ONLINE_DROPS:
            ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)
