-- The control plane's stores. Every write of an org runs in one unit as that org: its head row is claimed first
-- (`INSERT ... ON CONFLICT DO UPDATE`, the row lock held to commit), so an org's writes commit one at a time at
-- consecutive revisions, with the audit entry and the change event in the same unit.
--
-- Documents the Mongo stores keep as JSON text (policy layers, approvals, catalog items, installations, ledger
-- entries) stay text here: their digests and the stored-form probes read the text as written. Fields a query filters,
-- sorts or keys on are columns; identifiers that sort are COLLATE "C", the byte order Mongo sorts strings in.

-- `cpOrgHeads`: one row per org whose revision rises once per committed control-plane write.
CREATE TABLE harness.cp_org_heads (
  org_id text NOT NULL,
  revision bigint NOT NULL CHECK (revision >= 0),
  updated_at timestamptz NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id)
);

-- The deployment's revision (Mongo's `$deployment` head row): it moves with every write of a document all orgs share
-- (the AI models), so it is no org's and has no org_id. One row; a counter, no org data.
CREATE TABLE harness.cp_deployment_head (
  id text COLLATE "C" PRIMARY KEY CHECK (id = 'deployment'),
  revision bigint NOT NULL CHECK (revision >= 0),
  updated_at timestamptz NOT NULL
);

-- `cpPolicyLayers`: an org's layer per scope; body NULL is a deleted layer. decision_fence is the approver fence's
-- counter (QA21-F2), never part of the policy.
CREATE TABLE harness.cp_policy_layers (
  org_id text NOT NULL,
  scope text COLLATE "C" NOT NULL,
  kind text COLLATE "C" NOT NULL,
  revision bigint NOT NULL CHECK (revision > 0),
  body text,
  decision_fence bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, scope)
);
CREATE INDEX cp_policy_layers_by_kind ON harness.cp_policy_layers (org_id, kind, scope) WHERE body IS NOT NULL;

-- `cpPolicyHistory`: one row per committed revision of a scope, append-only.
CREATE TABLE harness.cp_policy_history (
  org_id text NOT NULL,
  scope text COLLATE "C" NOT NULL,
  revision bigint NOT NULL CHECK (revision > 0),
  body text,
  at timestamptz NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, scope, revision)
);

-- `cpMigrationLedger`: one row per org a data migration applied (and the capability migration's per-org marker).
CREATE TABLE harness.cp_migration_ledger (
  org_id text NOT NULL,
  migration_id text COLLATE "C" NOT NULL,
  applied_at timestamptz NOT NULL,
  entry text NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, migration_id)
);
-- `entries(migrationId)`: every org one migration applied, read by its revert as the jobs role.
CREATE INDEX cp_migration_ledger_by_migration ON harness.cp_migration_ledger (migration_id, org_id);

-- `cpCatalogItems`: an org's catalog rows; sort_name is the display name lower-cased (the list order and `q`).
CREATE TABLE harness.cp_catalog_items (
  org_id text NOT NULL,
  ref text COLLATE "C" NOT NULL,
  kind text COLLATE "C" NOT NULL,
  publisher text COLLATE "C" NOT NULL,
  name text COLLATE "C" NOT NULL,
  version text COLLATE "C" NOT NULL,
  sort_name text COLLATE "C" NOT NULL,
  created_at bigint NOT NULL,
  deprecated boolean NOT NULL,
  body text NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, ref),
  UNIQUE (org_id, kind, publisher, name, version)
);
CREATE INDEX cp_catalog_items_order ON harness.cp_catalog_items (org_id, kind, sort_name, ref);

-- `cpInstallations` and `cpInstallationHistory`: the row is the installation's JSON text; indexed fields are copies.
CREATE TABLE harness.cp_installations (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  item text COLLATE "C" NOT NULL,
  kind text COLLATE "C" NOT NULL,
  owner text COLLATE "C" NOT NULL,
  status text COLLATE "C" NOT NULL,
  installed_via text COLLATE "C",
  revision bigint NOT NULL CHECK (revision > 0),
  body text NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX cp_installations_status ON harness.cp_installations (org_id, status, kind);
CREATE INDEX cp_installations_owner ON harness.cp_installations (org_id, owner);
CREATE INDEX cp_installations_parent ON harness.cp_installations (org_id, installed_via) WHERE installed_via IS NOT NULL;
CREATE INDEX cp_installations_item ON harness.cp_installations (org_id, item);

CREATE TABLE harness.cp_installation_history (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  revision bigint NOT NULL CHECK (revision > 0),
  body text NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, id, revision)
);

-- `cpGrants`: one row per subject. `usersOf` scans the org's grants (its key prefix): an org holds one per subject
-- that was ever granted, and only the impact preview asks.
CREATE TABLE harness.cp_grants (
  org_id text NOT NULL,
  subject text COLLATE "C" NOT NULL,
  entries jsonb NOT NULL,
  revision bigint NOT NULL CHECK (revision > 0),
  region text NOT NULL,
  PRIMARY KEY (org_id, subject)
);

-- `cpCapabilityLedger`: what the capability migration and sync wrote, so revert removes exactly that.
CREATE TABLE harness.cp_capability_ledger (
  org_id text NOT NULL,
  object text COLLATE "C" NOT NULL CHECK (object IN ('installation', 'grant')),
  id text COLLATE "C" NOT NULL,
  revision bigint NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, object, id)
);

-- `cpBindings`: binding metadata, never a secret; doc is the binding as the store returns it. refresh_* is the token
-- refresher replica's claim (QA23-F2), cleared by any write.
CREATE TABLE harness.cp_bindings (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  installation text COLLATE "C" NOT NULL,
  owner text COLLATE "C" NOT NULL,
  status text COLLATE "C" NOT NULL,
  expires_at bigint,
  revision bigint NOT NULL CHECK (revision > 0),
  refresh_holder text,
  refresh_until bigint,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, id)
);
-- One binding that is not revoked per installation and owner; revoked ones stay as history (LIVE_BINDING_STATUSES).
CREATE UNIQUE INDEX cp_bindings_live_owner ON harness.cp_bindings (org_id, installation, owner)
  WHERE status IN ('pending', 'active', 'expiring', 'expired', 'error');
CREATE INDEX cp_bindings_installation ON harness.cp_bindings (org_id, installation, owner, id);
CREATE INDEX cp_bindings_for_owner ON harness.cp_bindings (org_id, owner, id);
-- The refresher's scan across orgs (the jobs role): live bindings by expiry.
CREATE INDEX cp_bindings_due ON harness.cp_bindings (expires_at, id)
  WHERE status IN ('active', 'expiring') AND expires_at IS NOT NULL;

SELECT kernel.apply_rls('harness.cp_org_heads', 'org');
SELECT kernel.register_internal('harness.cp_deployment_head');
SELECT kernel.apply_rls('harness.cp_policy_layers', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.cp_policy_history', 'org');
SELECT kernel.apply_rls('harness.cp_migration_ledger', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.cp_catalog_items', 'org');
SELECT kernel.apply_rls('harness.cp_installations', 'org');
SELECT kernel.apply_rls('harness.cp_installation_history', 'org');
SELECT kernel.apply_rls('harness.cp_grants', 'org');
SELECT kernel.apply_rls('harness.cp_capability_ledger', 'org');
SELECT kernel.apply_rls('harness.cp_bindings', 'org', p_jobs => true);

GRANT SELECT, INSERT, UPDATE, DELETE ON harness.cp_org_heads, harness.cp_deployment_head, harness.cp_policy_layers,
  harness.cp_policy_history, harness.cp_migration_ledger, harness.cp_catalog_items,
  harness.cp_installations, harness.cp_installation_history, harness.cp_grants, harness.cp_capability_ledger,
  harness.cp_bindings TO pipeshub_node;
-- The cross-org reads: the policy layers' org scan, a migration's ledger and the refresher's due bindings.
GRANT SELECT ON harness.cp_policy_layers, harness.cp_migration_ledger, harness.cp_bindings TO pipeshub_jobs;
