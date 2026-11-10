-- Write state of a stored file: a counter that only ever grows and the list of writers that may be mid-write. Nothing reads or writes
-- them yet, and the repository's column list does not name them, so no document read returns them.
-- Constant defaults: existing rows read 0 and an empty list without a rewrite, and an older build's inserts keep working.
ALTER TABLE content.documents
  ADD COLUMN write_seq bigint NOT NULL DEFAULT 0,
  ADD COLUMN writers jsonb NOT NULL DEFAULT '[]';
