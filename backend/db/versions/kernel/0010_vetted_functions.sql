-- The owner of the SECURITY DEFINER functions in schema kernel. Normally created by the role bootstrap; created here, NOLOGIN,
-- only when missing (a bare database, or tests). The functions themselves are in vetted/ (generated from vetted/manifest.yaml).
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_vetted_kernel') THEN
    CREATE ROLE pipeshub_vetted_kernel NOLOGIN;
    GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the vetted owner of schema kernel is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
SET LOCAL ROLE pipeshub_owner;
