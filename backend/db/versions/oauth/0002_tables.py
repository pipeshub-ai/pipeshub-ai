"""oauth: apps, access and refresh tokens, authorization codes, device authorizations, service-account delegations and their log"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "oauth_0002"
down_revision = "oauth_0001"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel IN ("
               "'oauth.delegation_events'::regclass, 'oauth.service_account_delegations'::regclass, 'oauth.device_authorizations'::regclass, "
               "'oauth.authorization_codes'::regclass, 'oauth.refresh_tokens'::regclass, 'oauth.access_tokens'::regclass, 'oauth.apps'::regclass)")
    op.execute("DROP TABLE oauth.delegation_events, oauth.service_account_delegations, oauth.device_authorizations, oauth.authorization_codes, "
               "oauth.refresh_tokens, oauth.access_tokens, oauth.apps")
    op.execute("DROP DOMAIN oauth.oid24")
