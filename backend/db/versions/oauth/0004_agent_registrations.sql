-- An agent registration of the install's organisation: the email the agent named, the SHA-256 of its three secrets, and where the claim stands.
-- `claimed_org_id` is the organisation of the person who approved; `purge_at` is when the sweeper may delete the row (after the longest assertion it backs).
CREATE TABLE oauth.agent_registrations (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  claim_email text NOT NULL,
  claim_token_hash text NOT NULL,
  attempt_token_hash text NOT NULL,
  user_code_hash text NOT NULL,
  attempt_expires_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  purge_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'claimed', 'consumed', 'expired', 'revoked')),
  failed_attempts int NOT NULL DEFAULT 0,
  last_polled_at timestamptz,
  claimed_user_id oauth.oid24,
  claimed_org_id text,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, claim_token_hash),
  UNIQUE (org_id, attempt_token_hash)
);
CREATE INDEX agent_registrations_by_purge ON oauth.agent_registrations (org_id, purge_at);
CREATE INDEX agent_registrations_by_user ON oauth.agent_registrations (org_id, claimed_user_id) WHERE claimed_user_id IS NOT NULL;

SELECT kernel.apply_rls('oauth.agent_registrations', 'org', p_jobs => true);

GRANT SELECT, INSERT, UPDATE, DELETE ON oauth.agent_registrations TO pipeshub_node;
GRANT SELECT, DELETE ON oauth.agent_registrations TO pipeshub_jobs;
