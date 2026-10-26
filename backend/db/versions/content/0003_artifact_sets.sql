-- A row that stands for one content revision of a virtual record (a directory of files under its root, not a single stored file):
-- {"virtualRecordId": ..., "rev": ..., "root": ...}. NULL for every other row.
ALTER TABLE content.documents ADD COLUMN artifact_set jsonb;
