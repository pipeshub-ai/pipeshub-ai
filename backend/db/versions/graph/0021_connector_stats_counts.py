"""graph: the kept record counts of each connector and collection (graph.stats_*), and the triggers on graph.nodes and graph.edges that log what may change them"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "graph_0021"
down_revision = "graph_0020"
branch_labels = None
depends_on = None
older_code_compatible = ("adds four tables, functions and triggers on graph.nodes and graph.edges that only insert into one of those tables, as the writer of graph.*, "
                         "which holds the insert; older code never reads them and writes the graph as before (its log rows wait for a build that projects them)")


def upgrade():
    run_sql(__file__)


def downgrade():
    for t in ("nodes", "edges"):
        for e in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER stats_{t}_{e} ON graph.{t}")
    op.execute("DROP TRIGGER graph_stats_apps_restore ON graph.stats_apps")
    op.execute("DELETE FROM kernel.trigger_exemptions WHERE proc IN ('graph.stats_log_change()'::regprocedure, 'graph.stats_app_restored()'::regprocedure)")
    op.execute("DROP FUNCTION graph.stats_log_change()")
    op.execute("DROP FUNCTION graph.stats_app_restored()")
    op.execute("""DELETE FROM kernel.rls_registry WHERE rel IN ('graph.stats_changes'::regclass, 'graph.stats_records'::regclass, 'graph.stats_counts'::regclass,
        'graph.stats_apps'::regclass)""")
    op.execute("DROP TABLE graph.stats_changes, graph.stats_records, graph.stats_counts, graph.stats_apps")
