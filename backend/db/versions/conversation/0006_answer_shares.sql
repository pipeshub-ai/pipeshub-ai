-- How an answer row is shared in place: the asker's consent to show one version of it to more people (consent), whom a
-- share with people covers, in the membership each had then (consent_to), and members' asks to see it (share_requests).
-- consent_to and share_requests are read only by the server, never returned to a caller. Nullable or with a constant
-- default: existing rows read as never shared, and an older build's inserts and updates keep working.
ALTER TABLE conversation.chat_messages
  ADD COLUMN consent jsonb,
  ADD COLUMN consent_to jsonb NOT NULL DEFAULT '[]',
  ADD COLUMN share_requests jsonb NOT NULL DEFAULT '[]';
