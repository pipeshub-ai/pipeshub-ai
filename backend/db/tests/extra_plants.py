"""Plants written independently of the checks by an adversarial review. Each must be reported by the catalog or the surface."""
SECDEF = "LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp"
BLIND = [
 ("new schema, USAGE+CREATE to PUBLIC", "CREATE SCHEMA evil; GRANT USAGE, CREATE ON SCHEMA evil TO PUBLIC"),
 ("permissive policy through a SECDEF helper ignoring org",
  f"CREATE FUNCTION public.allow(text) RETURNS boolean {SECDEF} STABLE AS 'SELECT true'; "
  "CREATE POLICY p_extra ON kt.cfg FOR SELECT TO pipeshub_app USING (public.allow(org_id))"),
 ("column added later to a global table + column UPDATE grant",
  "ALTER TABLE kt.tokens ADD COLUMN secret text; GRANT SELECT (secret), UPDATE (secret) ON kt.tokens TO pipeshub_node"),
 ("security_barrier (not invoker) view over org-scoped table, granted",
  "CREATE VIEW kt.v_docs WITH (security_barrier) AS SELECT * FROM kt.docs; ALTER VIEW kt.v_docs OWNER TO pipeshub_owner; GRANT SELECT ON kt.v_docs TO pipeshub_node"),
 ("trigger whose SECDEF function is owned by the superuser",
  "CREATE FUNCTION public.trg() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp "
  "AS 'BEGIN INSERT INTO kt.tokens VALUES (md5(random()::text), NEW.org_id, NEW.k); RETURN NEW; END'; "
  "CREATE TRIGGER t_cfg AFTER INSERT ON kt.cfg FOR EACH ROW EXECUTE FUNCTION public.trg()"),
 ("set-returning SECDEF function inside a policy",
  f"CREATE FUNCTION public.orgs() RETURNS SETOF text {SECDEF} STABLE AS 'SELECT DISTINCT org_id FROM kt.cfg'; "
  "CREATE POLICY p_srf ON kt.cfg FOR SELECT TO pipeshub_app USING (org_id IN (SELECT public.orgs()))"),
 ("legacy INHERITS child of an org-scoped table, granted",
  "CREATE TABLE kt.cfg_child () INHERITS (kt.cfg); ALTER TABLE kt.cfg_child OWNER TO pipeshub_owner; GRANT SELECT ON kt.cfg_child TO pipeshub_node"),
 ("CHECK constraint calling a SECDEF function",
  f"CREATE FUNCTION public.chk2(text) RETURNS boolean {SECDEF} IMMUTABLE AS 'SELECT true'; "
  "ALTER TABLE kt.cfg ADD CONSTRAINT c_leak CHECK (public.chk2(v)) NOT VALID"),
 ("range type with a SECDEF subtype_diff",
  f"CREATE FUNCTION public.fdiff(float8, float8) RETURNS float8 {SECDEF} IMMUTABLE AS 'SELECT $1 - $2'; "
  "CREATE TYPE public.frange AS RANGE (subtype = float8, subtype_diff = public.fdiff)"),
 ("cron-like job table in kernel, INSERT to node",
  "CREATE TABLE kernel.job_queue (cmd text, run_as text); ALTER TABLE kernel.job_queue OWNER TO pipeshub_owner; GRANT INSERT ON kernel.job_queue TO pipeshub_node"),
 ("TABLESAMPLE read fn by the vetted role, ignores org, registered read",
  "CREATE FUNCTION kt.sample() RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp "
  "AS 'SELECT body FROM kt.docs TABLESAMPLE SYSTEM (100)'; ALTER FUNCTION kt.sample() OWNER TO pipeshub_vetted_kt; "
  "REVOKE ALL ON FUNCTION kt.sample() FROM PUBLIC; GRANT EXECUTE ON FUNCTION kt.sample() TO pipeshub_app; "
  "SELECT kernel.register_function('kt.sample()', 'read')"),
 ("SECDEF function added to an allowed extension (plpgsql)",
  f"CREATE FUNCTION kt.hid2() RETURNS SETOF text {SECDEF} AS 'SELECT token_hash FROM kt.tokens'; ALTER EXTENSION plpgsql ADD FUNCTION kt.hid2()"),
 ("look-alike role \"pipeshub_owner \" (trailing space) BYPASSRLS LOGIN",
  "CREATE ROLE \"pipeshub_owner \" LOGIN BYPASSRLS; GRANT USAGE ON SCHEMA kt TO \"pipeshub_owner \"; GRANT SELECT ON kt.docs TO \"pipeshub_owner \""),
 ("upper-case look-alike \"PIPESHUB_NODE\" login in pipeshub_node",
  "CREATE ROLE \"PIPESHUB_NODE\" LOGIN; GRANT pipeshub_node TO \"PIPESHUB_NODE\""),
 ("CREATE on tablespace pg_default to a service role", "GRANT CREATE ON TABLESPACE pg_default TO pipeshub_node"),
 ("look-alike table kt.\"cfg \" copy of org data, granted",
  "CREATE TABLE kt.\"cfg \" AS SELECT * FROM kt.cfg; ALTER TABLE kt.\"cfg \" OWNER TO pipeshub_owner; GRANT SELECT ON kt.\"cfg \" TO pipeshub_node"),
 ("existing grant upgraded WITH GRANT OPTION", "GRANT SELECT ON kt.docs TO pipeshub_connectors WITH GRANT OPTION"),
 ("write function EXECUTE to PUBLIC", "GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO PUBLIC"),
 ("language 'internal' made trusted + USAGE to owner",
  "UPDATE pg_language SET lanpltrusted = true WHERE lanname = 'internal'; GRANT USAGE ON LANGUAGE internal TO pipeshub_owner"),
 ("login role search_path setting puts a writable schema first",
  "ALTER ROLE pipeshub_node_login SET search_path = public, kt"),
 ("database-level app.org_id default", "ALTER DATABASE t_rls SET app.org_id = 'bbbbbbbbbbbbbbbbbbbbbbbb'"),
 ("pg_read_server_files to a service role", "GRANT pg_read_server_files TO pipeshub_node"),
 ("pg_execute_server_program to jobs", "GRANT pg_execute_server_program TO pipeshub_jobs"),
 ("implicit cast through a SECDEF function",
  f"CREATE TYPE public.tt AS (a text); CREATE FUNCTION public.c2(public.tt) RETURNS text {SECDEF} IMMUTABLE AS 'SELECT ($1).a'; "
  "CREATE CAST (public.tt AS text) WITH FUNCTION public.c2(public.tt) AS IMPLICIT"),
 ("existing org policy retargeted TO PUBLIC (roles only)",
  "DO $$DECLARE p text; BEGIN SELECT policyname INTO p FROM pg_policies WHERE schemaname='kt' AND tablename='cfg' AND cmd='ALL' LIMIT 1; "
  "EXECUTE format('ALTER POLICY %I ON kt.cfg TO PUBLIC', p); END$$"),
 ("RLS disabled on a 2nd-level hash leaf", "ALTER TABLE kt.docs_h0 NO FORCE ROW LEVEL SECURITY; ALTER TABLE kt.docs_h0 DISABLE ROW LEVEL SECURITY"),
 ("direct SELECT on a 2nd-level hash leaf", "GRANT SELECT ON kt.docs_h1 TO pipeshub_node"),
 ("large object with ACL to a service role", "SELECT lo_create(424242); GRANT SELECT ON LARGE OBJECT 424242 TO pipeshub_node"),
 ("column DEFAULT calling a SECDEF function that reads a global table",
  f"CREATE FUNCTION public.dflt() RETURNS text {SECDEF} VOLATILE AS 'SELECT string_agg(token_hash, '','') FROM kt.tokens'; "
  "ALTER TABLE kt.cfg ALTER COLUMN v SET DEFAULT public.dflt()"),
 ("expression index calling a SECDEF function",
  f"CREATE FUNCTION public.ix(text) RETURNS text {SECDEF} IMMUTABLE AS 'SELECT lower($1)'; CREATE INDEX cfg_ix ON kt.cfg (public.ix(v))"),
]
