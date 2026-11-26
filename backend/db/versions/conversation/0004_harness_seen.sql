-- ADR-UI-01 D2 (UI-B1): when each person last opened the session, `{userId: ms}`, which the agent harness compares
-- with the end of their latest run to say "finished, not seen yet" on their chat list. One mark per person, only
-- moved forward; it goes with the session, so erasing the session erases it.
ALTER TABLE conversation.chat_sessions ADD COLUMN harness_seen jsonb;
