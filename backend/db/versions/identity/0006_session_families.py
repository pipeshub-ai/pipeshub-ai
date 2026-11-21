"""identity: session families, so a refresh token is replaced on every refresh and a replayed one ends its sign-in"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "identity_0006"
down_revision = "identity_0005"
branch_labels = None
depends_on = None
older_code_compatible = "only adds a table; older code never reads it and keeps refreshing without rotation"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'identity.session_families'::regclass")
    op.execute("DROP TABLE identity.session_families")
