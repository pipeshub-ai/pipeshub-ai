"""platform: a queue delivery records, bound by a MAC, that a consumer already opened and verified its message"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0008"
down_revision = "platform_0007"
branch_labels = None
depends_on = None
older_code_compatible = (
    "two nullable columns that earlier code neither reads nor writes; earlier consumers keep judging every message by its age, "
    "so one that claims a message after a wait longer than the envelope lifetime refuses it as they did before"
)


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("ALTER TABLE platform.queue_deliveries DROP COLUMN admission_mac, DROP COLUMN admission_ms")
