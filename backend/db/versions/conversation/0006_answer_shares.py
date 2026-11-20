"""conversation: the share columns of a message (consent, consent_to, share_requests)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "conversation_0006"
down_revision = "conversation_0005"
branch_labels = None
depends_on = None
older_code_compatible = ("adds a nullable column and two more with a constant default; older code never names them, "
                         "and its inserts and updates use the defaults")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("ALTER TABLE conversation.chat_messages DROP COLUMN share_requests, DROP COLUMN consent_to, DROP COLUMN consent")
