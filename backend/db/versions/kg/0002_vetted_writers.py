"""kg: the vetted writers: the only code that writes the kg tables"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kg_0002"
down_revision = "kg_0001"
branch_labels = None
depends_on = None
older_code_compatible = "adds functions only; older code does not call them"

WRITERS = ("w_nodes_upsert", "w_records_upsert", "w_records_delete", "w_links_set", "w_links_delete", "w_grants_set", "w_grants_delete",
           "w_closure_set", "w_closure_delete", "w_changes_ack", "w_changes_delete", "w_projection_set", "w_classes_set",
           "w_class_literals_set", "w_app_facts_set", "w_app_cross_set")


def upgrade():
    run_vetted(__file__, *WRITERS)


def downgrade():
    op.execute("DELETE FROM kernel.vetted_write_pins WHERE proc::oid IN (SELECT oid FROM pg_proc WHERE pronamespace = 'kg'::regnamespace)")
    for name in WRITERS:
        op.execute(f"DROP FUNCTION IF EXISTS kg.{name} CASCADE")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
