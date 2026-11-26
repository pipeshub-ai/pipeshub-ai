"""conversation: the agent harness's fields of sessions and messages"""
from alembic import op
from db.lib.ddl import ensure_index
from db.lib.run_sql import run_sql

revision = "conversation_0003"
down_revision = "conversation_0002"
branch_labels = None
depends_on = None
older_code_compatible = "adds nullable columns without defaults and non-unique indexes; older code neither reads nor writes them"


def upgrade():
    run_sql(__file__)
    bind = op.get_bind()
    # Only sessions whose erasure is still under way carry the marker (erasure deletes the row): the sweeper's
    # lost-enqueue pass reads a small index.
    ensure_index(bind, "conversation.chat_sessions_erasure_requested",
                 "CREATE INDEX chat_sessions_erasure_requested ON conversation.chat_sessions (org_id, erasure_requested_at, id) "
                 "WHERE erasure_requested_at IS NOT NULL")
    # Retention: an org's live sessions idle since before a cut-off, oldest first.
    ensure_index(bind, "conversation.chat_sessions_idle",
                 "CREATE INDEX chat_sessions_idle ON conversation.chat_sessions (org_id, last_activity_at, id) WHERE NOT is_deleted")
    # Erasure marks an erased upload in the org's other messages (QA-15G), and the carriers read finds who holds one.
    ensure_index(bind, "conversation.chat_messages_attachment_records",
                 "CREATE INDEX chat_messages_attachment_records ON conversation.chat_messages "
                 "USING gin (org_id, attachments jsonb_path_ops) WHERE attachments <> '[]'::jsonb")


def downgrade():
    op.execute("DROP INDEX conversation.chat_sessions_erasure_requested, conversation.chat_sessions_idle, "
               "conversation.chat_messages_attachment_records")
    op.execute("ALTER TABLE conversation.chat_messages DROP COLUMN sender_id, DROP COLUMN label, DROP COLUMN harness")
    op.execute("ALTER TABLE conversation.chat_sessions DROP COLUMN harness_version, DROP COLUMN harness_ledger, "
               "DROP COLUMN erasure_requested_at, DROP COLUMN coding_engine, DROP COLUMN coding_engine_pin, DROP COLUMN repository")
