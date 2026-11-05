"""identity: the jobs role reads the organisation gauges through functions that return only the gauge columns"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "identity_0005"
down_revision = "identity_0004"
branch_labels = None
depends_on = None
older_code_compatible = "only adds functions and a vetted read policy; older code still reads the three tables, and the jobs role keeps that access"

FUNCTIONS = ("gauge_user_counts", "gauge_live_orgs", "gauge_auth_configs")
TABLES = "'identity.users'::regclass, 'identity.orgs'::regclass, 'identity.org_auth_config'::regclass"


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    op.execute("DROP FUNCTION " + ", ".join(f"identity.{name}()" for name in FUNCTIONS))
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    # identity_0004's flags: only identity.orgs opts into the vetted read
    op.execute(f"""DO $$ DECLARE r kernel.rls_registry; BEGIN
      FOR r IN SELECT * FROM kernel.rls_registry WHERE rel = ANY (ARRAY[{TABLES}])
      LOOP PERFORM kernel.apply_rls(r.rel, r.class, true, r.rel = 'identity.orgs'::regclass, '5s', r.vetted_write); END LOOP; END $$""")
    op.execute("REVOKE SELECT ON identity.users, identity.org_auth_config FROM pipeshub_vetted_identity")
