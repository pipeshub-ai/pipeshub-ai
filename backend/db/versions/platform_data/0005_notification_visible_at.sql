-- A notification can be created now and become visible later. `visible_at` NULL means visible at once (every row so far); a row with
-- a `visible_at` ahead of now is left out of every read and change (list, counts, mark all read, by-id changes). Nullable with no
-- default: existing rows are not rewritten. The partitions take the column from the parent; the index (org_id, assigned_to, status,
-- visible_at) WHERE NOT is_deleted is built by the revision itself (db.lib.ddl.ensure_partitioned_index).
ALTER TABLE platform.notifications ADD COLUMN visible_at timestamptz;

