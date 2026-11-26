"""harness: a person's runs across their conversations, newest first (ADR-UI-01 D1)"""
from alembic import op
from db.lib.ddl import ensure_index
from db.lib.run_sql import run_sql

revision = "harness_0010"
down_revision = "harness_0009"
branch_labels = None
depends_on = None
older_code_compatible = "adds two non-unique indexes on harness.runs; older code neither needs nor notices them"


def upgrade():
    run_sql(__file__)
    bind = op.get_bind()
    # A person's runs newest first with an exact page cursor (the run id breaks a tie of created_at).
    ensure_index(bind, "harness.runs_by_actor_cursor",
                 "CREATE INDEX runs_by_actor_cursor ON harness.runs (org_id, actor_id, created_at DESC, run_id DESC)")
    # The same, for one agent's Runs tab.
    ensure_index(bind, "harness.runs_by_actor_agent",
                 "CREATE INDEX runs_by_actor_agent ON harness.runs (org_id, actor_id, agent_key, created_at DESC, run_id DESC)")


def downgrade():
    op.execute("DROP INDEX harness.runs_by_actor_cursor, harness.runs_by_actor_agent")
