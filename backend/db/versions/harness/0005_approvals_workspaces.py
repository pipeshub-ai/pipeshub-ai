"""harness: engine sign-ins and choices, workspaces, memories and schedules"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0005"
down_revision = "harness_0004"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    tables = (
        "harness.engine_signins",
        "harness.personal_choices",
        "harness.workspace_heads",
        "harness.workspace_manifests",
        "harness.workspace_keys",
        "harness.workspace_writers",
        "harness.memories",
        "harness.schedules",
        "harness.schedule_firings",
        "harness.schedule_counters",
    )
    op.execute("DELETE FROM kernel.rls_registry WHERE rel IN (" + ", ".join(f"'{t}'::regclass" for t in tables) + ")")
    op.execute("DROP TABLE " + ", ".join(tables))
