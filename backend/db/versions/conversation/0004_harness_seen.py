"""conversation: when each person last opened a session (ADR-UI-01 D2)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "conversation_0004"
down_revision = "conversation_0003"
branch_labels = None
depends_on = None
older_code_compatible = "adds one nullable column without a default; older code neither reads nor writes it"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("ALTER TABLE conversation.chat_sessions DROP COLUMN harness_seen")
