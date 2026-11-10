-- The owner of the SECURITY DEFINER functions in schema identity (next revision). Normally created by the role bootstrap; created here,
-- NOLOGIN, only when missing (a bare database, or tests). The row policies below name it.
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_vetted_identity') THEN
    CREATE ROLE pipeshub_vetted_identity NOLOGIN;
    GRANT pipeshub_vetted_identity TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the vetted owner of schema identity is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
SET LOCAL ROLE pipeshub_owner;

-- People, organisations, groups and sign-in state. Every id column is a 24-hex ObjectId string (migrated rows keep their Mongo _id);
-- C collation keeps ObjectId time order. org_id stays plain text (the kernel's format check). Optional fields are NULL when absent and
-- left out of the API object (the row mappers do that).
CREATE DOMAIN identity.oid24 AS text COLLATE "C" CHECK (VALUE ~ '^[0-9a-f]{24}$');

-- 'org-<n>' slugs.
CREATE SEQUENCE identity.org_slug_seq START 1001;

CREATE TABLE identity.orgs (
  org_id text PRIMARY KEY,
  slug text NOT NULL DEFAULT 'org-' || nextval('identity.org_slug_seq'),
  registered_name text,
  short_name text,
  domain text NOT NULL,
  contact_email text NOT NULL,
  account_type text NOT NULL CHECK (account_type IN ('individual', 'business')),
  phone_number text,
  permanent_address jsonb,
  on_boarding_status text CHECK (on_boarding_status IN ('configured', 'notConfigured', 'skipped')),
  is_deleted boolean NOT NULL DEFAULT false,
  deleted_by_user text,
  admin_role_guard_at timestamptz,
  signup_channel text,
  signup_referrer_domain text,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE identity.users (
  org_id text NOT NULL,
  id identity.oid24 NOT NULL,
  slug text NOT NULL,
  full_name text,
  first_name text,
  last_name text,
  middle_name text,
  email text NOT NULL CHECK (email = lower(email)),
  mobile text,
  has_logged_in boolean NOT NULL DEFAULT false,
  designation text,
  kind text NOT NULL DEFAULT 'human' CHECK (kind IN ('human', 'service')),
  description text,
  is_disabled boolean NOT NULL DEFAULT false,
  restore_op_id text,
  role text NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
  address jsonb,
  is_deleted boolean NOT NULL DEFAULT false,
  deleted_by text,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, email),            -- soft-deleted rows included, as today
  UNIQUE (org_id, slug),
  CONSTRAINT users_service_is_not_admin CHECK (NOT (kind = 'service' AND role = 'admin'))
);
CREATE INDEX users_by_kind ON identity.users (org_id, kind) WHERE NOT is_deleted;
CREATE INDEX users_by_created ON identity.users (org_id, created_at, id) WHERE NOT is_deleted;

CREATE TABLE identity.user_groups (
  org_id text NOT NULL,
  id identity.oid24 NOT NULL,
  slug text NOT NULL,
  name text NOT NULL,
  type text NOT NULL CHECK (type IN ('admin', 'standard', 'everyone', 'custom')),
  is_deleted boolean NOT NULL DEFAULT false,
  deleted_by identity.oid24,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, slug)
);
CREATE UNIQUE INDEX user_groups_live_name ON identity.user_groups (org_id, name) WHERE NOT is_deleted;
CREATE INDEX user_groups_by_type ON identity.user_groups (org_id, type) WHERE NOT is_deleted;

CREATE TABLE identity.user_group_members (
  org_id text NOT NULL,
  group_id identity.oid24 NOT NULL,
  user_id identity.oid24 NOT NULL,
  pos int NOT NULL,                  -- the index in the group's member list
  PRIMARY KEY (org_id, group_id, user_id),
  FOREIGN KEY (org_id, group_id) REFERENCES identity.user_groups (org_id, id) ON DELETE CASCADE
);
CREATE INDEX user_group_members_by_user ON identity.user_group_members (org_id, user_id, group_id);

CREATE TABLE identity.org_auth_config (
  org_id text PRIMARY KEY,
  id identity.oid24 NOT NULL,
  domain text,
  removed_sign_in_methods text[] NOT NULL DEFAULT '{}',   -- sign-in methods a release removed from the policy; the admin notice names them
  auth_steps jsonb NOT NULL CHECK (jsonb_typeof(auth_steps) = 'array' AND jsonb_array_length(auth_steps) BETWEEN 1 AND 3),
  is_deleted boolean NOT NULL DEFAULT false,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

-- sign-ins, sign-outs, lockouts and password and role changes of a person in this organisation. The newest session-ending row is read on
-- every authenticated request.
CREATE TABLE identity.user_activities (
  org_id text NOT NULL,
  id identity.oid24 NOT NULL,
  user_id identity.oid24,
  email text,
  activity_type text NOT NULL,
  login_mode text,
  ip_address text NOT NULL,
  is_deleted boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX user_activities_by_user ON identity.user_activities (org_id, user_id, activity_type, created_at DESC, id DESC) WHERE NOT is_deleted;

-- a password-reset link that was used: inserting the row is how a request claims the link
CREATE TABLE identity.used_reset_links (
  org_id text NOT NULL,
  link_hash text NOT NULL,
  user_id text NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, link_hash)
);
CREATE INDEX used_reset_links_expiry ON identity.used_reset_links (expires_at);

CREATE TABLE identity.org_logos (
  org_id text PRIMARY KEY,
  id identity.oid24 NOT NULL,
  logo text CHECK (octet_length(logo) <= 3000000),    -- uploads are capped far below this; the check keeps a future caller from storing anything
  logo_url text,
  mime_type text,
  version int NOT NULL DEFAULT 0
);
CREATE TABLE identity.user_pictures (
  org_id text NOT NULL,
  user_id identity.oid24 NOT NULL,
  id identity.oid24 NOT NULL,
  pic text CHECK (octet_length(pic) <= 2000000),
  pic_url text,
  mime_type text,
  version int NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, user_id)
);

-- next number of the per-organisation counters behind 'user-<n>' and 'usergroup-<n>' slugs (kind is the model name)
CREATE TABLE identity.slug_counters (
  org_id text NOT NULL,
  kind text NOT NULL,
  next_value int NOT NULL,
  PRIMARY KEY (org_id, kind)
);

-- The sign-in secrets and counters of one account. Read and written as the account's organisation, like every other table here.
CREATE TABLE identity.user_credentials (
  org_id text NOT NULL,
  user_id identity.oid24 NOT NULL,
  password_hash text,
  wrong_count int NOT NULL DEFAULT 0,
  is_blocked boolean NOT NULL DEFAULT false,
  block_expires_at timestamptz,
  otp_hash text,
  otp_valid_until bigint,
  force_new_password boolean NOT NULL DEFAULT false,
  ip_address text,
  created_at timestamptz NOT NULL DEFAULT date_trunc('milliseconds', now()),
  updated_at timestamptz NOT NULL DEFAULT date_trunc('milliseconds', now()),
  PRIMARY KEY (org_id, user_id),
  FOREIGN KEY (org_id, user_id) REFERENCES identity.users (org_id, id) ON DELETE CASCADE
);
CREATE INDEX user_credentials_blocked ON identity.user_credentials (org_id, user_id) WHERE is_blocked;

SELECT kernel.apply_rls('identity.orgs', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('identity.users', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.user_groups', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.user_group_members', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.org_auth_config', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.user_activities', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.used_reset_links', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.org_logos', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.user_pictures', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.slug_counters', 'org', p_jobs => true);
SELECT kernel.apply_rls('identity.user_credentials', 'org', p_jobs => true);

SELECT kernel.waive_sequence('identity.org_slug_seq', 'identity.orgs', 'slug', 'one value per organisation, as the shared counter always was', 'security-review');

GRANT USAGE ON SCHEMA identity TO pipeshub_node, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.orgs, identity.users, identity.user_groups, identity.user_group_members, identity.org_auth_config,
  identity.user_activities, identity.used_reset_links, identity.org_logos, identity.user_pictures, identity.slug_counters, identity.user_credentials TO pipeshub_node, pipeshub_jobs;
GRANT USAGE ON SEQUENCE identity.org_slug_seq TO pipeshub_node;
