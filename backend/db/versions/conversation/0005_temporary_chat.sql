-- Temporary chats stay out of history lists. A constant false default keeps
-- existing rows and older inserts unchanged. expires_at is the crash backstop.
ALTER TABLE conversation.chat_sessions
  ADD COLUMN is_temporary boolean NOT NULL DEFAULT false,
  ADD COLUMN expires_at timestamptz;
