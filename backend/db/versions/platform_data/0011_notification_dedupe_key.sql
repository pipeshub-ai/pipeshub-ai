-- A notification may carry a key its recipient holds once (`dedupe_key`): an event delivered again finds the row it stored and stores
-- nothing. Nullable with no default: existing rows are not rewritten. The table is partitioned by created_at, so no unique index can hold
-- (org_id, assigned_to, dedupe_key) alone: a writer takes a transaction lock per key, then looks the key up through the index
-- (org_id, assigned_to, dedupe_key) WHERE dedupe_key IS NOT NULL, built by the revision itself (db.lib.ddl.ensure_partitioned_index).
ALTER TABLE platform.notifications ADD COLUMN dedupe_key text;
