-- The owner of the SECURITY DEFINER functions in schema platform (job state). Normally created by the role bootstrap; created here,
-- NOLOGIN, only when missing (a bare database, or tests).
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_vetted_platform') THEN
    CREATE ROLE pipeshub_vetted_platform NOLOGIN;
    GRANT pipeshub_vetted_platform TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the vetted owner of schema platform is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
SET LOCAL ROLE pipeshub_owner;

-- Job state (platform.jobs) is written by exactly three functions of this schema's vetted owner: the operator starts and finishes a job
-- (the migrate role runs them), and the running job saves its progress. No application or jobs role holds a privilege on the table
-- (catalog check 96), so a job cannot open its own gate or change its own state.
UPDATE kernel.rls_registry SET vetted_read = true, vetted_write = true WHERE rel = 'platform.jobs'::regclass;
GRANT USAGE ON SCHEMA platform TO pipeshub_vetted_platform;
GRANT SELECT, INSERT, UPDATE ON platform.jobs TO pipeshub_vetted_platform;
