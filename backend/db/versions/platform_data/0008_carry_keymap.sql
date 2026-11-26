-- docstore-migrate's key map (R45-CARRY, ADR-DOCMIG-01): for a table that does not keep the MongoDB _id (the Slack thread links, the
-- workspace heads, manifests and keys), which row each source document became. A change-stream delete names only the _id, so the
-- follower reads the row to remove here (an erasure during the follow must not survive the move), and a rollback gives every row
-- back the id its document had. Written by the import role in the unit that writes the rows; nothing of the application reads it.
CREATE TABLE platform.docstore_keymap (
  org_id text NOT NULL,
  collection text COLLATE "C" NOT NULL,
  source_id text COLLATE "C" NOT NULL,
  source_oid boolean NOT NULL,
  row_key text COLLATE "C" NOT NULL,
  PRIMARY KEY (org_id, collection, source_id)
);
-- a delete event does not say which organisation the document was in
CREATE INDEX docstore_keymap_by_source ON platform.docstore_keymap (collection, source_id);
CREATE UNIQUE INDEX docstore_keymap_by_row ON platform.docstore_keymap (org_id, collection, row_key);

SELECT kernel.apply_rls('platform.docstore_keymap', 'org');
SELECT kernel.grant_import_access('platform.docstore_keymap');
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.docstore_keymap TO pipeshub_import;

-- the harness tables the copy carries (data keys and workspaces, the coding-engine choices, the audit chain)
SELECT kernel.grant_import_access('harness.personal_choices');
SELECT kernel.grant_import_access('harness.data_keys');
SELECT kernel.grant_import_access('harness.workspace_heads');
SELECT kernel.grant_import_access('harness.workspace_manifests');
SELECT kernel.grant_import_access('harness.workspace_keys');
SELECT kernel.grant_import_access('harness.audit_records');
SELECT kernel.grant_import_access('harness.audit_heads');
SELECT kernel.grant_import_access('harness.audit_pending');
SELECT kernel.grant_import_access('harness.audit_anchors');
GRANT USAGE ON SCHEMA harness TO pipeshub_import;
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.personal_choices, harness.data_keys,
  harness.workspace_heads, harness.workspace_manifests, harness.workspace_keys, harness.audit_records, harness.audit_heads,
  harness.audit_pending, harness.audit_anchors TO pipeshub_import;
