"""kg: the change capture on graph.nodes and graph.edges, and the narrowed insert grant on the change log"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kg_0004"
down_revision = "kg_0003"
branch_labels = None
depends_on = None
older_code_compatible = ("adds statement triggers on graph.nodes and graph.edges that only insert into kg.graph_changes, as the writing role, and are exempted by body hash; "
                         "older code writes the graph unchanged")


def upgrade():
    run_sql(__file__)


def downgrade():
    for table, events in (("nodes", ("insert", "update", "delete", "truncate")), ("edges", ("insert", "update", "delete", "truncate"))):
        for ev in events:
            op.execute(f"DROP TRIGGER IF EXISTS kg_{table}_{ev} ON graph.{table}")
    op.execute("DROP TRIGGER IF EXISTS kg_changes_stamp ON kg.graph_changes")
    op.execute("DELETE FROM kernel.trigger_exemptions WHERE proc::oid IN (SELECT oid FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname IN ('log_graph_change', 'stamp_change'))")
    op.execute("DROP FUNCTION IF EXISTS kg.log_graph_change(), kg.refuse_truncate(), kg.stamp_change()")
    op.execute("REVOKE USAGE ON SEQUENCE kg.graph_changes_id_seq FROM pipeshub_connectors, pipeshub_indexing, pipeshub_query")
    op.execute("REVOKE INSERT ON kg.graph_changes FROM pipeshub_connectors, pipeshub_indexing, pipeshub_query")
    op.execute("SELECT kernel.require_vetted_org_policy('kg.graph_changes')")
