"""harness: erasure stores"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0008"
down_revision = "harness_0007"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    tables = [
        "harness.session_erasures", "harness.agent_erasures", "harness.principal_erasures",
        "harness.erasure_backfill_plans", "harness.erasure_backfill_items",
        "harness.erasure_backfill_markers",
    ]
    rels = ", ".join(f"'{t}'::regclass" for t in tables)
    op.execute(f"DELETE FROM kernel.rls_registry WHERE rel IN ({rels})")
    op.execute(f"DROP TABLE {', '.join(tables)}")
