-- Tables of the content module. Ids are 24-hex ObjectId strings (C collation keeps their time order for keyset cursors); org_id stays
-- plain text (the kernel's format check). Optional fields are nullable columns: a NULL column is a key the row mapper leaves out.
CREATE DOMAIN content.oid24 AS text COLLATE "C" CHECK (VALUE ~ '^[0-9a-f]{24}$');

-- Plain tables: org_id leads every key and index, so a deployment that serves many orgs can partition them by org later.
CREATE TABLE content.documents (
  org_id text NOT NULL,
  id content.oid24 NOT NULL,
  document_name text NOT NULL,
  alternate_document_name text,
  document_path text COLLATE "C",                  -- byte order, so a path prefix is a range: >= 'p/' AND < 'p0'
  is_versioned_file boolean NOT NULL DEFAULT false,
  mutation_count int NOT NULL DEFAULT 1,
  permissions text CHECK (permissions IN ('owner', 'editor', 'commentator', 'readonly')),
  initiator_user_id content.oid24,
  initiator_unset boolean NOT NULL DEFAULT false,   -- the row was created without the field (emitted as null when it was given as null)
  size_in_bytes bigint,
  mime_type text,
  extension text,
  version_history jsonb NOT NULL DEFAULT '[]',
  custom_metadata jsonb NOT NULL DEFAULT '[]',
  connector_id text,                               -- the customMetadata entry with key connectorId, kept in step by the repository
  current_version int NOT NULL DEFAULT 0,
  tags text[] NOT NULL DEFAULT '{}',
  deleted_by_user_id text,
  is_deleted boolean NOT NULL DEFAULT false,
  s3 jsonb,
  azure_blob jsonb,
  local jsonb,
  storage_vendor text NOT NULL CHECK (storage_vendor IN ('s3', 'azureBlob', 'local')),
  idempotency_key text,
  idempotency_fingerprint text,
  upload_lease_token text,
  upload_lease_expires_at timestamptz,
  awaiting_direct_upload boolean,
  artifact_set jsonb,                              -- {"virtualRecordId", "rev", "root"}: the row stands for one content revision of a virtual record; NULL for every other row
  retired_at bigint,                               -- server clock in ms of the retire mark: a newer stored write replaced this row, so it takes no more in-place buffer writes; NULL while live
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);

-- Two presigns of one revision racing each other settle on one row; the first column also serves listing and purging a record's revisions.
CREATE UNIQUE INDEX documents_artifact_set ON content.documents (org_id, (artifact_set->>'virtualRecordId'), (artifact_set->>'rev'))
  WHERE artifact_set IS NOT NULL;

CREATE INDEX documents_by_name ON content.documents (org_id, document_name);
CREATE INDEX documents_by_path ON content.documents (org_id, document_path);
CREATE INDEX documents_by_connector ON content.documents (org_id, connector_id) WHERE connector_id IS NOT NULL;
-- a keyed create is made once per (org, principal, key); a service token has no principal, which counts as one principal
CREATE UNIQUE INDEX documents_idempotency ON content.documents (org_id, (COALESCE(initiator_user_id, '000000000000000000000000')), idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE TABLE content.projects (
  org_id text NOT NULL,
  id content.oid24 NOT NULL,
  user_id content.oid24 NOT NULL,                  -- the owner
  name text NOT NULL,
  description text,
  icon text,
  color text,
  instructions text,
  knowledge_scope jsonb NOT NULL DEFAULT '{"apps": [], "kb": [], "recordGroups": [], "records": []}',
  applied_filters jsonb NOT NULL DEFAULT '{"apps": [], "kb": [], "recordGroups": [], "records": []}',
  tools text[] NOT NULL DEFAULT '{}',
  linked_knowledge_base_id text,                   -- emitted as null when unset
  visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'org')),
  chat_sharing text NOT NULL DEFAULT 'private' CHECK (chat_sharing IN ('private', 'members')),
  is_pinned boolean NOT NULL DEFAULT false,         -- the legacy project-wide pin; it counts only as the owner's own pin
  pinned_by content.oid24[] NOT NULL DEFAULT '{}',  -- the people who pinned the project in their own list
  is_archived boolean NOT NULL DEFAULT false,
  archived_by content.oid24,
  is_deleted boolean NOT NULL DEFAULT false,
  deleted_by content.oid24,
  last_activity_at timestamptz NOT NULL,
  metadata jsonb,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE TABLE content.project_members (
  org_id text NOT NULL,
  project_id content.oid24 NOT NULL,
  principal_type text NOT NULL CHECK (principal_type IN ('user', 'team')),
  principal_id text NOT NULL,                      -- a user's id, or a team's graph id (a UUID or all_<org>)
  role text NOT NULL CHECK (role IN ('viewer', 'editor')),
  added_by content.oid24 NOT NULL,
  added_at timestamptz NOT NULL,
  pos int NOT NULL,                                -- the index in the project's members array
  PRIMARY KEY (org_id, project_id, principal_type, principal_id),
  CHECK (principal_id <> '' AND (principal_type = 'team' OR principal_id ~ '^[0-9a-f]{24}$')),
  FOREIGN KEY (org_id, project_id) REFERENCES content.projects (org_id, id) ON DELETE CASCADE
);
CREATE INDEX projects_by_owner ON content.projects (org_id, user_id, name);
CREATE INDEX projects_listing ON content.projects (org_id, last_activity_at DESC, id DESC) WHERE NOT is_deleted;
CREATE INDEX projects_org_visible ON content.projects (org_id, visibility) WHERE NOT is_deleted;
CREATE INDEX project_members_by_principal ON content.project_members (org_id, principal_id, principal_type);

SELECT kernel.apply_rls('content.documents', 'org');
SELECT kernel.apply_rls('content.projects', 'org');
SELECT kernel.apply_rls('content.project_members', 'org');
GRANT USAGE ON SCHEMA content TO pipeshub_node;
GRANT SELECT, INSERT, UPDATE, DELETE ON content.documents, content.projects, content.project_members TO pipeshub_node;
