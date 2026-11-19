-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.org_leaf_request()
RETURNS TABLE (requested boolean) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  IF NOT EXISTS (SELECT 1 FROM retrieval.placement_requests r WHERE r.org_id = v_org) THEN
    BEGIN
      INSERT INTO retrieval.placement_requests (org_id) VALUES (v_org);
    EXCEPTION WHEN unique_violation THEN NULL;         -- two writers of one org asked at once
    END;
  ELSE
    UPDATE retrieval.placement_requests r SET failed_at = NULL, note = NULL, attempts = 0, next_try_at = NULL
      WHERE r.org_id = v_org AND r.done_at IS NULL AND r.failed_at < now() - interval '30 seconds';
  END IF;
  RETURN QUERY SELECT true;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.org_leaf_request() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.org_leaf_request() TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.org_leaf_request()', 'write', 'pipeshub_indexing', 'org', false);
