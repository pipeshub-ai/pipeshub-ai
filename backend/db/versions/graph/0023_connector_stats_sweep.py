"""graph: the checkpoint of an organisation's first count of connector stats (graph.stats_sweeps), so that count runs in bounded pages and resumes where it stopped"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "graph_0023"
down_revision = "graph_0022"
branch_labels = None
depends_on = None
older_code_compatible = "adds a table; older code never reads or writes it"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP TRIGGER graph_stats_sweeps_restore ON graph.stats_sweeps")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'graph.stats_sweeps'::regclass")
    op.execute("DROP TABLE graph.stats_sweeps")
