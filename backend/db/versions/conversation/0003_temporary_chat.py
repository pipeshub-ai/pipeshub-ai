"""conversation: temporary chats are hidden from history and expire"""
from alembic import op
from db.lib.ddl import ensure_index
from db.lib.run_sql import run_sql

revision = "conversation_0003"
down_revision = "conversation_0002"
branch_labels = None
depends_on = None
older_code_compatible = "adds is_temporary and expires_at with defaults; older code never names them"


def upgrade():
    run_sql(__file__)
    ensure_index(
        op.get_bind(),
        "conversation.chat_sessions_temporary_expiry",
        "CREATE INDEX chat_sessions_temporary_expiry ON conversation.chat_sessions (expires_at) "
        "WHERE is_temporary AND expires_at IS NOT NULL",
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS conversation.chat_sessions_temporary_expiry")
    op.execute(
        "ALTER TABLE conversation.chat_sessions DROP COLUMN IF EXISTS expires_at, DROP COLUMN IF EXISTS is_temporary"
    )
