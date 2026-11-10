-- The access lists of kg (kg.scopes) and the stamp of a record of no connector (kg.records.r13).
-- A scope is a record or record group that has an access list of its own; it is written with the access stamps, by the vetted writers only (kg_0008), and read by the services.
-- r13: a record of no connector that only a grant on the record opens. NULL means not stamped (a reader takes the walk), as for every other stamp column.
ALTER TABLE kg.records ADD COLUMN r13 boolean;

CREATE TABLE kg.scopes (
  org_id text NOT NULL, sk bigint NOT NULL, kind smallint NOT NULL, app_sk bigint NOT NULL, restricted boolean NOT NULL, rule_restricted boolean NOT NULL, needs_grant boolean NOT NULL,
  deleted boolean NOT NULL, PRIMARY KEY (org_id, sk)
);
CREATE INDEX scopes_app ON kg.scopes (org_id, app_sk, sk);      -- one connector's rows: verify, and a restart that holds the whole organisation while it reads them
ALTER TABLE kg.scopes SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05, autovacuum_vacuum_insert_scale_factor = 0.02);

SELECT kernel.apply_rls('kg.scopes', 'org', p_vetted_write => true);
SELECT kernel.require_vetted_org_policy('kg.scopes');
GRANT SELECT ON kg.scopes TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
