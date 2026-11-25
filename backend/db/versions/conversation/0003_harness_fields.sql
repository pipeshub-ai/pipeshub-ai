-- The agent harness's fields of a conversation (WP109-FU-2), which the document store keeps on the same documents.
-- Sessions: the executor pin with the import ledger (`harnessVersion`, `harnessLedger`), the erasure marker of a delete
-- (`erasureRequestedAt`, E7B), and a chat's coding engine, its pin and its repository (SLASH3, C14.01, GIT-FLOW).
-- Messages: who sent a collaborator's turn (`senderId`, SS2), the durable answer's confidentiality label (`label`,
-- P2.5a: never read unless asked for by name) and the rest of the harness's fields as one document (`harness`: runId,
-- errorCode, errorServers, errorStop, truncatedBy, budgetScope, runOutcome, command, copied, foreignTurn).
ALTER TABLE conversation.chat_sessions
  ADD COLUMN harness_version text,
  ADD COLUMN harness_ledger jsonb,
  ADD COLUMN erasure_requested_at bigint,
  ADD COLUMN coding_engine text,
  ADD COLUMN coding_engine_pin text,
  ADD COLUMN repository jsonb;
ALTER TABLE conversation.chat_messages
  ADD COLUMN sender_id text COLLATE "C",
  ADD COLUMN label jsonb,
  ADD COLUMN harness jsonb;

-- Its indexes are built in the .py (ensure_index): the tables exist before this revision.
