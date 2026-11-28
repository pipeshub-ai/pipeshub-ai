-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_identity TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA identity TO pipeshub_vetted_identity;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA identity TO pipeshub_vetted_identity;
SET LOCAL ROLE pipeshub_vetted_identity;
CREATE FUNCTION identity.copied_org_slug_claim(p_org_id text, p_slug text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, identity, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_org_id IS NULL THEN RAISE EXCEPTION 'copied_org_slug_claim: p_org_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_org_id) NOT BETWEEN 24 AND 24 THEN RAISE EXCEPTION 'copied_org_slug_claim: p_org_id length must be 24..24' USING ERRCODE = '22023'; END IF;
  IF p_slug IS NULL THEN RAISE EXCEPTION 'copied_org_slug_claim: p_slug is required' USING ERRCODE = '22023'; END IF;
  IF length(p_slug) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'copied_org_slug_claim: p_slug length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM identity.org_slugs t WHERE t.org_id = p_org_id AND t.slug = p_slug) THEN
    RETURN false;
  END IF;
  INSERT INTO identity.org_slugs (org_id, slug) VALUES (p_org_id, p_slug);
  RETURN true;
END $fn$;
REVOKE ALL ON FUNCTION identity.copied_org_slug_claim(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.copied_org_slug_claim(text, text) TO pipeshub_import;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA identity FROM pipeshub_vetted_identity;
REVOKE pipeshub_vetted_identity FROM pipeshub_owner;
SELECT kernel.register_function('identity.copied_org_slug_claim(text, text)', 'write', 'pipeshub_import', 'global', false);
