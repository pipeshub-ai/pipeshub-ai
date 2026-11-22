"""conversation: chat sessions, members, messages, citations and saved searches"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "conversation_0002"
down_revision = "conversation_0001"
branch_labels = None
depends_on = "kernel_0011"
older_code_compatible = "creates new tables and their partitions; the dynamic statements only name objects it creates"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel IN ("
               "'conversation.semantic_search_members'::regclass, 'conversation.semantic_searches'::regclass, 'conversation.citations'::regclass, 'conversation.chat_messages'::regclass, 'conversation.chat_session_members'::regclass, 'conversation.chat_sessions'::regclass)")
    op.execute("DROP TABLE conversation.semantic_search_members, conversation.semantic_searches, conversation.citations, conversation.chat_messages, "
               "conversation.chat_session_members, conversation.chat_sessions")
    op.execute("DROP DOMAIN conversation.oid24")
    # an extension the administrator installed (bootstrap) is theirs to drop; only one this revision created goes
    for ext in ("btree_gin", "pg_trgm"):
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = '{ext}' AND pg_has_role(current_user, extowner, 'USAGE')) "
                   f"THEN DROP EXTENSION {ext}; END IF; END $$")
