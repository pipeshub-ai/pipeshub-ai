"""identity: organisations, accounts, groups, sign-in state and credentials"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "identity_0002"
down_revision = "identity_0001"
branch_labels = None
depends_on = "kernel_0011"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.sequence_waivers WHERE seq = 'identity.org_slug_seq'::regclass")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel IN ("
               "'identity.user_credentials'::regclass, 'identity.slug_counters'::regclass, "
               "'identity.user_pictures'::regclass, 'identity.org_logos'::regclass, 'identity.used_reset_links'::regclass, 'identity.user_activities'::regclass, "
               "'identity.org_auth_config'::regclass, 'identity.user_group_members'::regclass, 'identity.user_groups'::regclass, 'identity.users'::regclass, "
               "'identity.orgs'::regclass)")
    op.execute("DROP TABLE identity.user_credentials, identity.slug_counters, identity.user_pictures, "
               "identity.org_logos, identity.used_reset_links, identity.user_activities, identity.org_auth_config, identity.user_group_members, "
               "identity.user_groups, identity.users, identity.orgs")
    op.execute("DROP SEQUENCE identity.org_slug_seq")
    op.execute("DROP DOMAIN identity.oid24")
