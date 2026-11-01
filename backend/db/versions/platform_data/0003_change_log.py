"""platform data: the change log of the document-store tables (the reverse follower reads it after a move to PostgreSQL)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0003"
down_revision = "platform_data_0002"
branch_labels = None
depends_on = ("kernel_0013",)
older_code_compatible = "older code writes the same rows as before; the triggers only append to the new change log, which every role that writes these tables may insert into, and the dynamic statements name only objects this file creates"

# table, column that names the document to export again (the root row's id, or the parent's id for a child table)
TABLES = (
    ("identity.org_logos", "id"), ("identity.users", "id"), ("identity.user_credentials", "user_id"), ("identity.user_groups", "id"), ("identity.user_group_members", "group_id"),
    ("identity.org_auth_config", "id"), ("identity.user_activities", "id"), ("identity.used_reset_links", "link_hash"),
    ("identity.user_pictures", "id"), ("identity.slug_counters", "org_id"),
    ("conversation.chat_sessions", "id"), ("conversation.chat_session_members", "session_id"), ("conversation.chat_messages", "id"),
    ("conversation.semantic_searches", "id"), ("conversation.semantic_search_members", "search_id"), ("conversation.citations", "id"),
    ("content.documents", "id"), ("content.projects", "id"), ("content.project_members", "project_id"),
    ("platform.notifications", "id"), ("platform.mail_log", "id"), ("platform.connectors_config", "id"),
    ("oauth.apps", "id"), ("oauth.access_tokens", "id"), ("oauth.refresh_tokens", "id"), ("oauth.authorization_codes", "id"), ("oauth.device_authorizations", "id"),
    ("oauth.service_account_delegations", "id"), ("oauth.delegation_events", "id"), ("integrations.slack_threads", "thread_id"),
)


def upgrade():
    run_sql(__file__)
    for table, pk in TABLES:
        op.execute(f"CREATE TRIGGER docstore_change_log AFTER INSERT OR UPDATE OR DELETE ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION platform.docstore_log_change('{pk}', '{table}')")


def downgrade():
    for table, _ in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS docstore_change_log ON {table}")
    op.execute("DROP FUNCTION platform.docstore_log_change()")
    op.execute("DROP TABLE platform.docstore_changes")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
