"""content: per-document write state (a write counter and the writers in flight), kept out of every read"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "content_0003"
down_revision = "content_0002"
branch_labels = None
depends_on = None
older_code_compatible = "adds two columns with constant defaults; older code never names them and its inserts and updates use the defaults"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("ALTER TABLE content.documents DROP COLUMN writers, DROP COLUMN write_seq")
