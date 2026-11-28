-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_identity TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA identity TO pipeshub_vetted_identity;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA identity TO pipeshub_vetted_identity;
SET LOCAL ROLE pipeshub_vetted_identity;
CREATE FUNCTION identity.org_slug_claim(p_slug text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, identity, pg_temp
SET standard_conforming_strings = on
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_slug IS NULL THEN RAISE EXCEPTION 'org_slug_claim: p_slug is required' USING ERRCODE = '22023'; END IF;
  IF length(p_slug) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'org_slug_claim: p_slug length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN false; END IF;       -- no org (or someone else's): no rows, the same answer
  IF length(p_slug) > 64 OR p_slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$' OR p_slug ~ '^org-[0-9]+$'
     OR EXISTS (SELECT 1 FROM identity.orgs o WHERE o.slug = p_slug) THEN
    RETURN false;
  END IF;
  BEGIN
    INSERT INTO identity.org_slugs (org_id, slug) VALUES (v_org, p_slug);
  EXCEPTION WHEN unique_violation THEN
    RETURN false;
  END;
  RETURN true;
END $fn$;
REVOKE ALL ON FUNCTION identity.org_slug_claim(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.org_slug_claim(text) TO pipeshub_node;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA identity FROM pipeshub_vetted_identity;
REVOKE pipeshub_vetted_identity FROM pipeshub_owner;
SELECT kernel.register_function('identity.org_slug_claim(text)', 'write', 'pipeshub_node', 'org', false);
