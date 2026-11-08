-- A narrow service role for the processes that only read their configuration (embedding, docling, parsing, extraction): it can use the
-- key-value table and call the fence, and holds nothing on any org-scoped table. Its LOGIN is created by the deployment bootstrap
-- (PG_KV_PASSWORD); a deployment without it runs those processes as `query`.
-- Normally created by the deployment bootstrap; created here only when missing (a bare database), as kernel_0001 does for the others.
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_kv') THEN
    CREATE ROLE pipeshub_kv NOLOGIN;
    GRANT pipeshub_app TO pipeshub_kv WITH INHERIT TRUE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the role pipeshub_kv is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
SET LOCAL ROLE pipeshub_owner;

UPDATE kernel.settings SET services = array_append(services, 'pipeshub_kv'::name) WHERE NOT ('pipeshub_kv'::name = ANY (services));

-- the fence and the profile reader are owned by the schema's vetted role: only it may grant on them (the owner borrows it for this transaction)
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE;
SET LOCAL ROLE pipeshub_vetted_kernel;
GRANT EXECUTE ON FUNCTION kernel.assert_profile_epoch(int), kernel.profile_epoch() TO pipeshub_kv;

-- the unit of work parses its prologue as the LOGIN, which needs USAGE on the fence's schema and EXECUTE on the fence (see kernel_0007)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_kv_login') THEN
    GRANT EXECUTE ON FUNCTION kernel.assert_profile_epoch(int) TO pipeshub_kv_login;
  END IF;
END $$;
SET LOCAL ROLE pipeshub_owner;
REVOKE pipeshub_vetted_kernel FROM pipeshub_owner;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_kv_login') THEN
    GRANT USAGE ON SCHEMA kernel TO pipeshub_kv_login;
  END IF;
END $$;
