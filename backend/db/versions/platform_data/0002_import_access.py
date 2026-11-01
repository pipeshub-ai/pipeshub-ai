"""platform data: the import role may write the document-store tables (the one-off copy of an existing install, docstore-migrate)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0002"
down_revision = "platform_data_0001"
branch_labels = None
# the tables it grants on live in these branches; kernel_0012 provides the import policy
depends_on = ("kernel_0012", "identity_0003", "conversation_0002", "content_0002", "oauth_0002", "integrations_0002", "platform_0002")

TABLES = (
    "identity.orgs, identity.org_logos, identity.users, identity.user_credentials, identity.user_groups, identity.user_group_members, identity.org_auth_config, "
    "identity.user_activities, identity.used_reset_links, identity.user_pictures, identity.slug_counters, "
    "conversation.chat_sessions, conversation.chat_session_members, conversation.chat_messages, conversation.semantic_searches, "
    "conversation.semantic_search_members, conversation.citations, "
    "content.documents, content.projects, content.project_members, platform.notifications, platform.mail_log, platform.connectors_config, "
    "platform.outbox, oauth.apps, oauth.access_tokens, oauth.refresh_tokens, oauth.authorization_codes, oauth.device_authorizations, oauth.service_account_delegations, "
    "oauth.delegation_events, integrations.slack_threads"
)


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute(f"""DO $$ DECLARE t regclass; p regclass; BEGIN
  FOR t IN SELECT unnest(string_to_array('{TABLES}', ', '))::regclass LOOP
    FOR p IN SELECT t UNION SELECT relid FROM pg_partition_tree(t) LOOP
      EXECUTE format('DROP POLICY IF EXISTS import_all ON %s', p);
    END LOOP;
    UPDATE kernel.rls_registry SET import_access = false WHERE rel = t;
  END LOOP;
END $$""")
    op.execute(f"REVOKE ALL ON {TABLES} FROM pipeshub_import")
    op.execute("GRANT pipeshub_vetted_platform TO pipeshub_owner WITH SET TRUE")
    op.execute("SET LOCAL ROLE pipeshub_vetted_platform")
    op.execute("REVOKE EXECUTE ON FUNCTION platform.job_checkpoint(bigint, text) FROM pipeshub_import")
    op.execute("SET LOCAL ROLE pipeshub_owner")
    op.execute("REVOKE pipeshub_vetted_platform FROM pipeshub_owner")
    op.execute("REVOKE USAGE ON SCHEMA identity, conversation, content, platform, oauth, integrations FROM pipeshub_import")
