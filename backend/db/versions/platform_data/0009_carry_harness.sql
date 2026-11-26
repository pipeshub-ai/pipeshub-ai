-- R46-CARRY (ADR-DOCMIG-01 decision 6): the key map's deployment-wide form, and the import role's access to the harness tables the
-- remaining carriers write (durable runs and their journal and payloads, the ledgers, people's data and erasure, the control plane).
--
-- A row no org owns (a sandbox reap, an erasure backfill plan, the control plane's deployment head) has no org_id, so the org-scoped
-- key map cannot hold its entry: its org_id is checked as an org id, and a made-up one would be an org the row policies know nothing
-- of. This is the same entry without an organisation. Written by the import role in the unit that writes the rows; nothing of the
-- application reads it.
CREATE TABLE platform.docstore_keymap_deployment (
  collection text COLLATE "C" NOT NULL,
  source_id text COLLATE "C" NOT NULL,
  source_oid boolean NOT NULL,
  row_key text COLLATE "C" NOT NULL,
  PRIMARY KEY (collection, source_id)
);
CREATE UNIQUE INDEX docstore_keymap_deployment_by_row ON platform.docstore_keymap_deployment (collection, row_key);
SELECT kernel.register_internal('platform.docstore_keymap_deployment');
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.docstore_keymap_deployment TO pipeshub_import;

-- org-scoped tables: the import role's row policy, then its privileges
SELECT kernel.grant_import_access(t::regclass) FROM unnest(ARRAY[
  'harness.runs', 'harness.run_events', 'harness.session_counters', 'harness.next_runs', 'harness.session_events',
  'harness.stream_counters', 'harness.journal_payloads', 'harness.session_forks', 'harness.sandbox_snapshots',
  'harness.command_receipts', 'harness.admission_receipts', 'harness.admission_quotas',
  'harness.budget_accounts', 'harness.budget_reservations', 'harness.engine_signins', 'harness.memories', 'harness.schedules',
  'harness.schedule_firings',
  'harness.schedule_counters', 'harness.inbox_preferences', 'harness.run_end_notices',
  'harness.session_erasures', 'harness.agent_erasures', 'harness.principal_erasures', 'harness.erasure_backfill_items',
  'harness.erasure_backfill_markers',
  'harness.cp_org_heads', 'harness.cp_policy_layers', 'harness.cp_policy_history', 'harness.cp_migration_ledger',
  'harness.cp_catalog_items', 'harness.cp_installations', 'harness.cp_installation_history', 'harness.cp_grants',
  'harness.cp_capability_ledger', 'harness.cp_bindings'
]) AS t;
GRANT SELECT, INSERT, UPDATE, DELETE ON
  harness.runs, harness.run_events, harness.session_counters, harness.next_runs, harness.session_events,
  harness.stream_counters, harness.journal_payloads, harness.session_forks, harness.sandbox_snapshots,
  harness.command_receipts, harness.admission_receipts, harness.admission_quotas,
  harness.budget_accounts, harness.budget_reservations, harness.engine_signins, harness.memories, harness.schedules,
  harness.schedule_firings,
  harness.schedule_counters, harness.inbox_preferences, harness.run_end_notices,
  harness.session_erasures, harness.agent_erasures, harness.principal_erasures, harness.erasure_backfill_items,
  harness.erasure_backfill_markers,
  harness.cp_org_heads, harness.cp_policy_layers, harness.cp_policy_history, harness.cp_migration_ledger,
  harness.cp_catalog_items, harness.cp_installations, harness.cp_installation_history, harness.cp_grants,
  harness.cp_capability_ledger, harness.cp_bindings
  TO pipeshub_import;

-- deployment-wide tables (no org_id, no row policy): privileges only
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.sandbox_reaps, harness.erasure_backfill_plans, harness.cp_deployment_head
  TO pipeshub_import;
