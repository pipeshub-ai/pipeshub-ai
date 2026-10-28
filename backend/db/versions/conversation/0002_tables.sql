-- Tables of the conversation aggregate. Every id column is a 24-hex ObjectId string (the migrated rows keep their Mongo _id);
-- C collation keeps ObjectId time order for keyset cursors. org_id stays plain text (the kernel's format check).
-- Plain tables: org_id leads every key and index, so a deployment that serves many orgs can partition them by org later.
CREATE DOMAIN conversation.oid24 AS text COLLATE "C" CHECK (VALUE ~ '^[0-9a-f]{24}$');
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA ext;
CREATE EXTENSION IF NOT EXISTS btree_gin WITH SCHEMA ext;

CREATE TABLE conversation.chat_sessions (
  org_id text NOT NULL,
  id conversation.oid24 NOT NULL,
  session_type text NOT NULL DEFAULT 'chat' CHECK (session_type IN ('chat', 'agent')),
  user_id conversation.oid24 NOT NULL,
  initiator conversation.oid24 NOT NULL,
  title text,
  is_shared boolean NOT NULL DEFAULT false,
  share_link text,
  is_deleted boolean NOT NULL DEFAULT false,
  deleted_by conversation.oid24,
  is_archived boolean NOT NULL DEFAULT false,
  archived_by conversation.oid24,
  last_activity_at timestamptz,
  status text,
  fail_reason text,
  active_run_id text,
  next_seq int NOT NULL DEFAULT 0,
  model_info jsonb NOT NULL DEFAULT '{"chatMode": "quick"}',
  conversation_errors jsonb NOT NULL DEFAULT '[]',
  metadata jsonb,
  agent_key text,
  conversation_source text CHECK (conversation_source = 'agent_chat'),
  compacted_summary text,
  compacted_at_turn_index int,
  compacted_at_timestamp bigint,
  project_id conversation.oid24,
  project_visibility text CHECK (project_visibility IN ('private', 'project')),
  is_pinned boolean NOT NULL DEFAULT false,  -- the owner's pin; the document store keeps `isPinned: true` on a pinned chat and no field on any other
  version int NOT NULL DEFAULT 0,
  nulls text[] NOT NULL DEFAULT '{}',   -- fields stored as an explicit null (archived_by after an unarchive), emitted as null
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);

CREATE TABLE conversation.chat_session_members (
  org_id text NOT NULL,
  session_id conversation.oid24 NOT NULL,
  user_id conversation.oid24 NOT NULL,
  access_level text NOT NULL CHECK (access_level IN ('read', 'write')),
  pos int NOT NULL,                      -- the index in the session's sharedWith array
  entry_id conversation.oid24 NOT NULL,  -- the _id the document store gave each sharedWith entry (the API serves it)
  PRIMARY KEY (org_id, session_id, user_id),
  FOREIGN KEY (org_id, session_id) REFERENCES conversation.chat_sessions (org_id, id) ON DELETE CASCADE
);

CREATE TABLE conversation.chat_messages (
  org_id text NOT NULL,
  session_id conversation.oid24 NOT NULL,
  seq int NOT NULL,
  id conversation.oid24 NOT NULL,
  message_type text NOT NULL CHECK (message_type IN ('user_query', 'bot_response', 'error', 'feedback', 'system', 'tool_call')),
  content text NOT NULL DEFAULT '',
  content_format text,
  status text,
  confidence text,
  citations jsonb NOT NULL DEFAULT '[]',
  follow_up_questions jsonb NOT NULL DEFAULT '[]',
  feedback jsonb NOT NULL DEFAULT '[]',
  metadata jsonb,
  model_info jsonb,
  applied_filters jsonb,
  attachments jsonb NOT NULL DEFAULT '[]',
  reference_data jsonb NOT NULL DEFAULT '[]',
  tools jsonb NOT NULL DEFAULT '[]',
  reasoning jsonb NOT NULL DEFAULT '[]',
  parts jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, session_id, seq),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, session_id) REFERENCES conversation.chat_sessions (org_id, id) ON DELETE CASCADE
);

CREATE TABLE conversation.citations (
  org_id text NOT NULL,
  id conversation.oid24 NOT NULL,
  content text NOT NULL,
  chunk_index int NOT NULL,
  citation_type text NOT NULL,
  record_id text NOT NULL,
  record_name text,
  metadata jsonb NOT NULL,               -- the whole metadata document, as the API serves it
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);

CREATE INDEX chat_sessions_owner_list ON conversation.chat_sessions (org_id, user_id, session_type, last_activity_at DESC NULLS LAST, id DESC)
  WHERE NOT is_deleted AND NOT is_archived;
CREATE INDEX chat_sessions_owner_pinned ON conversation.chat_sessions (org_id, user_id, session_type, is_pinned DESC, last_activity_at DESC NULLS LAST, id DESC)
  WHERE NOT is_deleted AND NOT is_archived;
CREATE INDEX chat_sessions_archived_list ON conversation.chat_sessions (org_id, user_id, session_type, last_activity_at DESC NULLS LAST, id DESC)
  WHERE is_archived AND NOT is_deleted;
CREATE INDEX chat_sessions_project_list ON conversation.chat_sessions (org_id, project_id, last_activity_at DESC NULLS LAST, id DESC)
  WHERE project_id IS NOT NULL AND NOT is_deleted;
CREATE INDEX chat_sessions_project_visible ON conversation.chat_sessions (org_id, project_id, session_type, last_activity_at DESC NULLS LAST, id DESC)
  WHERE project_visibility = 'project' AND NOT is_deleted AND NOT is_archived;
CREATE INDEX chat_sessions_agent ON conversation.chat_sessions (org_id, user_id, agent_key, last_activity_at DESC NULLS LAST)
  WHERE session_type = 'agent' AND NOT is_deleted;
CREATE INDEX chat_session_members_by_user ON conversation.chat_session_members (org_id, user_id, session_id);
-- the content search of the chat lists (a case-insensitive substring of any message of the organisation)
CREATE INDEX chat_messages_content_trgm ON conversation.chat_messages USING gin (org_id, content ext.gin_trgm_ops);
CREATE INDEX citations_by_record ON conversation.citations (org_id, record_id);

CREATE TABLE conversation.semantic_searches (
  org_id text NOT NULL,
  id conversation.oid24 NOT NULL,
  query text NOT NULL,
  result_limit int NOT NULL,
  user_id conversation.oid24 NOT NULL,
  citation_ids conversation.oid24[] NOT NULL DEFAULT '{}',
  records jsonb NOT NULL DEFAULT '{}',
  is_shared boolean NOT NULL DEFAULT false,
  share_link text,
  is_archived boolean NOT NULL DEFAULT false,
  archived_by conversation.oid24,
  version int NOT NULL DEFAULT 0,
  nulls text[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE TABLE conversation.semantic_search_members (
  org_id text NOT NULL,
  search_id conversation.oid24 NOT NULL,
  user_id conversation.oid24 NOT NULL,
  access_level text NOT NULL CHECK (access_level IN ('read', 'write')),
  pos int NOT NULL,
  entry_id conversation.oid24 NOT NULL,
  PRIMARY KEY (org_id, search_id, user_id),
  FOREIGN KEY (org_id, search_id) REFERENCES conversation.semantic_searches (org_id, id) ON DELETE CASCADE
);
CREATE INDEX semantic_searches_by_user ON conversation.semantic_searches (org_id, user_id, created_at DESC, id DESC);
CREATE INDEX semantic_search_members_by_user ON conversation.semantic_search_members (org_id, user_id, search_id);

SELECT kernel.apply_rls('conversation.chat_sessions', 'org', p_jobs => true);
SELECT kernel.apply_rls('conversation.chat_session_members', 'org', p_jobs => true);
SELECT kernel.apply_rls('conversation.chat_messages', 'org', p_jobs => true);
SELECT kernel.apply_rls('conversation.citations', 'org', p_jobs => true);
SELECT kernel.apply_rls('conversation.semantic_searches', 'org', p_jobs => true);
SELECT kernel.apply_rls('conversation.semantic_search_members', 'org', p_jobs => true);
-- one writer service role (node); the jobs role for retention and the migrator. Partitions carry no application privilege.
GRANT USAGE ON SCHEMA conversation TO pipeshub_node, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON conversation.chat_sessions, conversation.chat_session_members, conversation.chat_messages,
  conversation.citations, conversation.semantic_searches, conversation.semantic_search_members TO pipeshub_node, pipeshub_jobs;
