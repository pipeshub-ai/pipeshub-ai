"""identity: the jobs role keeps only what the organisation gauges read"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "identity_0004"
down_revision = "identity_0003"
branch_labels = None
depends_on = None
older_code_compatible = "only the jobs role loses privileges it never used, and the organisation gauges still read what they need"

TABLES = ("identity.orgs, identity.users, identity.user_groups, identity.user_group_members, identity.org_auth_config, identity.user_activities, "
          "identity.used_reset_links, identity.org_logos, identity.user_pictures, identity.slug_counters, identity.user_credentials")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute(f"GRANT USAGE ON SCHEMA identity TO pipeshub_jobs; GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLES} TO pipeshub_jobs")
    op.execute(f"""DO $$ DECLARE r kernel.rls_registry; BEGIN
      FOR r IN SELECT * FROM kernel.rls_registry WHERE rel = ANY (ARRAY[{", ".join(f"'{t.strip()}'::regclass" for t in TABLES.split(","))}])
      LOOP PERFORM kernel.apply_rls(r.rel, r.class, true, r.vetted_read, '5s', r.vetted_write); END LOOP; END $$""")
