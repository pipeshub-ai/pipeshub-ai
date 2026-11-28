"""conversation: the label columns of a message (label_ver, provenance, label_history)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "conversation_0005"
down_revision = "conversation_0004"
branch_labels = None
depends_on = None
older_code_compatible = ("adds a nullable column and two more, nullable or with a constant default; older code never names them, "
                         "and its inserts and updates use the defaults")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("ALTER TABLE conversation.chat_messages DROP COLUMN label_history, DROP COLUMN provenance, DROP COLUMN label_ver")
