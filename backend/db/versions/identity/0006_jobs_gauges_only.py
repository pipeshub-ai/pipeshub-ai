"""identity: the jobs role loses its SELECT on users, orgs and org_auth_config; it reads the organisation gauges through identity_0005's functions"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "identity_0006"
down_revision = "identity_0005"
branch_labels = None
depends_on = None
# No min_code_epoch: the first release with a PostgreSQL schema (v1.1.0) already shipped identity_0005, so no released build is refused.
older_code_compatible = "every released build, v1.1.0 onward, reads the gauges through the identity_0005 functions and never these tables"

TABLES = "'identity.users'::regclass, 'identity.orgs'::regclass, 'identity.org_auth_config'::regclass"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("GRANT SELECT ON identity.users, identity.orgs, identity.org_auth_config TO pipeshub_jobs")
    op.execute(f"""DO $$ DECLARE r kernel.rls_registry; BEGIN
      FOR r IN SELECT * FROM kernel.rls_registry WHERE rel = ANY (ARRAY[{TABLES}])
      LOOP PERFORM kernel.apply_rls(r.rel, r.class, true, r.vetted_read, '5s', r.vetted_write); END LOOP; END $$""")
