"""identity: the two questions asked before an organisation is bound (the install's organisation, and how many there are)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "identity_0003"
down_revision = "identity_0002"
branch_labels = None
depends_on = None

FUNCTIONS = (("oldest_org", ""), ("org_count", "boolean"))


def upgrade():
    run_vetted(__file__, *[name for name, _ in FUNCTIONS])


def downgrade():
    op.execute("DROP FUNCTION " + ", ".join(f"identity.{name}({args})" for name, args in FUNCTIONS))
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
