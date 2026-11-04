"""conversation: temporary chats are hidden from history and expire"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "conversation_0003"
down_revision = "conversation_0002"
branch_labels = None
depends_on = None
older_code_compatible = "adds is_temporary and expires_at with defaults; older code never names them"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP INDEX IF EXISTS conversation.chat_sessions_temporary_expiry")
    op.execute(
        "ALTER TABLE conversation.chat_sessions DROP COLUMN IF EXISTS expires_at, DROP COLUMN IF EXISTS is_temporary"
    )
