"""kg: the pure SQL helpers the projection reads documents and edges by"""
from db.lib.run_sql import run_sql

revision = "kg_0003"
down_revision = "kg_0002"
branch_labels = None
depends_on = None
older_code_compatible = "adds functions only; older code does not call them"


def upgrade():
    run_sql(__file__)


def downgrade():
    from alembic import op
    for sig in ("kg.proj_doc(text, jsonb)", "kg.edge_sem(text, jsonb)", "kg.edge_ends(text, text, text, text, text, jsonb)", "kg.coll_code(text)"):
        op.execute(f"DROP FUNCTION IF EXISTS {sig}")
