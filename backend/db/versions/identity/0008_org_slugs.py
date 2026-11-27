"""identity: a slug a caller chooses for a new organisation is unique across the install (identity.org_slugs, identity.org_slug_claim)"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "identity_0008"
down_revision = "identity_0007"
branch_labels = None
depends_on = None
older_code_compatible = "adds a table and two functions; older code never chooses a slug and never calls them"


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, "org_slug_claim", "copied_org_slug_claim")


def downgrade():
    op.execute("DROP FUNCTION identity.org_slug_claim(text), identity.copied_org_slug_claim(text, text)")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'identity.org_slugs'::regclass")
    op.execute("DROP TABLE identity.org_slugs")
