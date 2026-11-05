-- the jobs role reads the three tables only through the gauge functions: no table privilege, no jobs_all policy
REVOKE SELECT ON identity.users, identity.orgs, identity.org_auth_config FROM pipeshub_jobs;
DO $$ DECLARE r kernel.rls_registry; BEGIN
  FOR r IN SELECT * FROM kernel.rls_registry WHERE rel = ANY (ARRAY['identity.users'::regclass, 'identity.orgs'::regclass, 'identity.org_auth_config'::regclass])
  LOOP PERFORM kernel.apply_rls(r.rel, r.class, false, true, '5s', r.vetted_write); END LOOP;      -- vetted read: the functions' owner reads the tables
END $$;
