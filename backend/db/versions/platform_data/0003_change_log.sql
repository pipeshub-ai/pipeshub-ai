-- The change log (docstore_changes): one row per changed row of a document-store table, naming the DOCUMENT to export again (the
-- root row's id; a child row names its parent). After a move from MongoDB to PostgreSQL the reverse follower reads it in order and
-- writes those documents back to MongoDB, so a rollback after the application has written to PostgreSQL stays possible online.
-- Triggers (created by this revision) write it as the role that changed the row, under the same row policy; the copy (the import
-- role) and a restore are not logged. identity.orgs (written by a vetted function), the outbox (settled by the dispatcher all day)
-- and the person table are not triggered: the follower sweeps those collections instead. The log is trimmed by the follower, and by
-- db.maintain when no follower runs.
CREATE TABLE platform.docstore_changes (
  org_id text NOT NULL,
  id bigint GENERATED ALWAYS AS IDENTITY,
  rel text NOT NULL,
  pk text NOT NULL,
  op "char" NOT NULL CHECK (op IN ('I', 'U', 'D')),
  txid bigint NOT NULL,
  at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (org_id, id)
);
CREATE INDEX docstore_changes_by_id ON platform.docstore_changes (id);

SELECT kernel.apply_rls('platform.docstore_changes', 'org', p_jobs => true);
SELECT kernel.grant_import_access('platform.docstore_changes');
GRANT INSERT ON platform.docstore_changes TO pipeshub_node;
GRANT SELECT, INSERT, DELETE ON platform.docstore_changes TO pipeshub_jobs;      -- the TTL sweeper trims what the follower has not (14 days)
GRANT SELECT, DELETE ON platform.docstore_changes TO pipeshub_import;

CREATE FUNCTION platform.docstore_log_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp AS $fn$
DECLARE r jsonb;
BEGIN
  -- a cascaded delete runs as the owner: the parent's own row already names the document
  IF current_user IN ('pipeshub_import', 'pipeshub_restore', 'pipeshub_owner') THEN RETURN NULL; END IF;
  r := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
  INSERT INTO platform.docstore_changes (org_id, rel, pk, op, txid)
  VALUES (r ->> 'org_id', TG_ARGV[1], r ->> TG_ARGV[0], left(TG_OP, 1), txid_current());
  RETURN NULL;
END $fn$;
REVOKE ALL ON FUNCTION platform.docstore_log_change() FROM PUBLIC;
-- check 97 treats a trigger function as callable by PUBLIC and refuses one that writes. This one writes the change log under the row
-- policy of the role that changed the row, on tables no vetted function writes; the exemption is reviewed and pinned to this body.
SELECT kernel.exempt_trigger('platform.docstore_log_change()', 'writes the change log as the role that changed the row; triggers sit on tables no vetted function writes', 'docstore-migrate');
