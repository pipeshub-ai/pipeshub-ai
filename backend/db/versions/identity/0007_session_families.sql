-- A sign-in on one device and the refresh token that may continue it now (ADR-WEB-02 D9). Token ids only, never a token.
CREATE TABLE identity.session_families (
  org_id text NOT NULL,
  family_id text NOT NULL,
  user_id text NOT NULL,
  current_token_id text NOT NULL,
  previous_token_id text,
  rotated_at timestamptz,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  revoked_reason text,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, family_id)
);
CREATE INDEX session_families_by_expiry ON identity.session_families (expires_at);

SELECT kernel.apply_rls('identity.session_families', 'org', p_jobs => true);
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.session_families TO pipeshub_node;
-- the TTL sweeper removes families past their end
GRANT SELECT, DELETE ON identity.session_families TO pipeshub_jobs;
