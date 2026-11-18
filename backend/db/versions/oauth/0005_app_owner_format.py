"""oauth: the owner format of an app (NULL on apps an older release stored)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "oauth_0005"
down_revision = "oauth_0004"
branch_labels = None
depends_on = None
older_code_compatible = "adds a nullable column with no default that older code neither reads nor writes; its inserts leave it NULL, which marks them for the repair"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("ALTER TABLE oauth.apps DROP COLUMN owner_format")
