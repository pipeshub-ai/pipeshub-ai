-- Ids are 24-hex ObjectId strings; org_id is plain text. A column that is NULL is a key the row mapper leaves out.
CREATE DOMAIN oauth.oid24 AS text COLLATE "C" CHECK (VALUE ~ '^[0-9a-f]{24}$');

-- Every key leads with org_id. A client id or token hash is looked up in the install's organisation (a token endpoint binds it);
-- an extension that serves several organisations supplies a lookup that finds the owner of a key.
CREATE TABLE oauth.apps (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  slug text NOT NULL,
  client_id text NOT NULL,
  client_secret_encrypted text NOT NULL,
  name text NOT NULL,
  description text,
  created_by oauth.oid24 NOT NULL,
  token_identity_user_id oauth.oid24,
  redirect_uris text[] NOT NULL DEFAULT '{}',
  allowed_grant_types text[] NOT NULL DEFAULT '{authorization_code,refresh_token}',
  allowed_scopes text[] NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'revoked')),
  logo_url text,
  homepage_url text,
  privacy_policy_url text,
  terms_of_service_url text,
  is_confidential boolean NOT NULL DEFAULT true,
  is_dynamic boolean NOT NULL DEFAULT false,
  access_token_lifetime int NOT NULL DEFAULT 3600,
  refresh_token_lifetime int NOT NULL DEFAULT 2592000,
  knowledge_limit jsonb,            -- what the app may reach: NULL is everything the signed-in user can see; else {"apps", "recordGroups", "records"}
  is_deleted boolean NOT NULL DEFAULT false,
  deleted_by oauth.oid24,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, client_id),
  UNIQUE (org_id, slug)
);
CREATE INDEX apps_by_creator ON oauth.apps (org_id, created_by, created_at DESC) WHERE NOT is_deleted;
CREATE INDEX apps_by_identity ON oauth.apps (org_id, token_identity_user_id) WHERE token_identity_user_id IS NOT NULL AND NOT is_deleted;

CREATE TABLE oauth.access_tokens (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  token_hash text NOT NULL,
  client_id text NOT NULL,
  user_id oauth.oid24,
  scopes text[] NOT NULL,
  expires_at timestamptz NOT NULL,
  is_revoked boolean NOT NULL DEFAULT false,
  revoked_at timestamptz,
  revoked_by oauth.oid24,
  revoked_reason text,
  parent_refresh_token_id oauth.oid24,
  name text,
  last_used_at timestamptz,
  actor_user_id oauth.oid24,
  knowledge_limit jsonb,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, token_hash)
);
CREATE INDEX access_tokens_live_by_client ON oauth.access_tokens (org_id, client_id, user_id) WHERE NOT is_revoked;
CREATE INDEX access_tokens_by_user ON oauth.access_tokens (org_id, user_id, created_at DESC);
CREATE INDEX access_tokens_by_actor ON oauth.access_tokens (org_id, actor_user_id) WHERE NOT is_revoked AND actor_user_id IS NOT NULL;
CREATE INDEX access_tokens_by_expiry ON oauth.access_tokens (org_id, expires_at);

CREATE TABLE oauth.refresh_tokens (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  token_hash text NOT NULL,
  client_id text NOT NULL,
  user_id oauth.oid24 NOT NULL,
  scopes text[] NOT NULL,
  expires_at timestamptz NOT NULL,
  is_revoked boolean NOT NULL DEFAULT false,
  revoked_at timestamptz,
  revoked_by oauth.oid24,
  revoked_reason text,
  rotation_count int NOT NULL DEFAULT 0,
  previous_token_hash text,
  knowledge_limit jsonb,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, token_hash)
);
CREATE INDEX refresh_tokens_live_by_client ON oauth.refresh_tokens (org_id, client_id, user_id) WHERE NOT is_revoked;
CREATE INDEX refresh_tokens_by_user ON oauth.refresh_tokens (org_id, user_id) WHERE NOT is_revoked;
CREATE INDEX refresh_tokens_by_expiry ON oauth.refresh_tokens (org_id, expires_at);

-- The code is stored as its SHA-256 (hex): a copy of the table does not hold a redeemable code.
CREATE TABLE oauth.authorization_codes (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  code_hash text NOT NULL,
  code_challenge text,
  code_challenge_method text CHECK (code_challenge_method IN ('S256', 'plain')),
  client_id text NOT NULL,
  user_id oauth.oid24 NOT NULL,
  redirect_uri text NOT NULL,
  scopes text[] NOT NULL,
  expires_at timestamptz NOT NULL,
  is_used boolean NOT NULL DEFAULT false,
  used_at timestamptz,
  replayed_at timestamptz,
  knowledge_limit jsonb,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, code_hash)
);
CREATE INDEX authorization_codes_by_client_user ON oauth.authorization_codes (org_id, client_id, user_id);
CREATE INDEX authorization_codes_by_user ON oauth.authorization_codes (org_id, user_id) WHERE NOT is_used;
CREATE INDEX authorization_codes_by_expiry ON oauth.authorization_codes (org_id, expires_at);

-- A pending device authorization of the install's organisation. The code a person types is unique within it; a claimed row stays until it expires.
-- The knowledge limit is three lists, all NULL for no limit.
CREATE TABLE oauth.device_authorizations (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  device_code_hash text NOT NULL,
  user_code text NOT NULL,
  client_id text NOT NULL,
  scopes text[] NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied', 'claimed')),
  approved_user_id oauth.oid24,
  poll_interval int NOT NULL DEFAULT 5,
  last_polled_at timestamptz,
  limit_apps text[],
  limit_groups text[],
  limit_records text[],
  expires_at timestamptz NOT NULL,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, device_code_hash),
  UNIQUE (org_id, user_code)
);
CREATE INDEX device_authorizations_by_expiry ON oauth.device_authorizations (org_id, expires_at);

-- The allow-list that lets a service account act as one named person, and the log of every exchange attempt.
CREATE TABLE oauth.service_account_delegations (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  service_account_id oauth.oid24 NOT NULL,
  subject_user_id oauth.oid24 NOT NULL,
  granted_by oauth.oid24 NOT NULL,
  reason text CHECK (char_length(reason) <= 280),
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, service_account_id, subject_user_id)
);

-- Events are kept 400 days (the document store expired them by TTL); the key order serves "newest first" for an org and for one account.
CREATE TABLE oauth.delegation_events (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  service_account_id oauth.oid24 NOT NULL,
  subject_user_id oauth.oid24,
  requested_subject text NOT NULL CHECK (char_length(requested_subject) <= 320),
  outcome text NOT NULL CHECK (outcome IN ('issued', 'refused', 'granted', 'revoked')),
  refused_reason text CHECK (char_length(refused_reason) <= 120),
  acted_by oauth.oid24,
  note text CHECK (char_length(note) <= 280),
  scopes text[] NOT NULL DEFAULT '{}',
  access_token_id oauth.oid24,
  jti text,
  expires_at timestamptz,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX delegation_events_by_time ON oauth.delegation_events (org_id, created_at DESC);
CREATE INDEX delegation_events_by_account ON oauth.delegation_events (org_id, service_account_id, created_at DESC);

SELECT kernel.apply_rls('oauth.apps', 'org');
SELECT kernel.apply_rls('oauth.access_tokens', 'org', p_jobs => true);
SELECT kernel.apply_rls('oauth.refresh_tokens', 'org', p_jobs => true);
SELECT kernel.apply_rls('oauth.authorization_codes', 'org', p_jobs => true);
SELECT kernel.apply_rls('oauth.device_authorizations', 'org', p_jobs => true);
SELECT kernel.apply_rls('oauth.service_account_delegations', 'org');
SELECT kernel.apply_rls('oauth.delegation_events', 'org', p_jobs => true);   -- the sweeper removes events past their retention

GRANT USAGE ON SCHEMA oauth TO pipeshub_node, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON oauth.apps, oauth.access_tokens, oauth.refresh_tokens, oauth.authorization_codes, oauth.device_authorizations,
  oauth.service_account_delegations, oauth.delegation_events TO pipeshub_node;
GRANT SELECT, DELETE ON oauth.access_tokens, oauth.refresh_tokens, oauth.authorization_codes, oauth.device_authorizations, oauth.delegation_events TO pipeshub_jobs;
