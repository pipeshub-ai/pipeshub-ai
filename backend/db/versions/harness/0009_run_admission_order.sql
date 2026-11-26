-- The order runs were inserted in, the counterpart of `agentRuns`' insertion-ordered `_id`. A session's root runs are
-- admitted one at a time, yet two can carry the same `createdAt` millisecond; "newest first" (the answered run, the
-- labelled turns, the latest run a turn saw) breaks that tie by it, as Mongo breaks it by `_id`, never by the random
-- run id. Its index, runs_by_session, is built in the .py (ensure_index): the table exists before this revision.
ALTER TABLE harness.runs ADD COLUMN admission_order bigint GENERATED ALWAYS AS IDENTITY;
