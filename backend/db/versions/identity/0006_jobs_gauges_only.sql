-- the jobs role reads the organisation gauges only through identity_0005's functions: no table of the schema is readable to it
REVOKE SELECT ON identity.users, identity.orgs, identity.org_auth_config FROM pipeshub_jobs;

-- and no jobs_all policy is left on the three tables (the vetted read of the functions' owner stays)
DO $$ DECLARE r kernel.rls_registry; BEGIN
  FOR r IN SELECT * FROM kernel.rls_registry WHERE rel = ANY (ARRAY['identity.users'::regclass, 'identity.orgs'::regclass, 'identity.org_auth_config'::regclass])
  LOOP PERFORM kernel.apply_rls(r.rel, r.class, false, r.vetted_read, '5s', r.vetted_write); END LOOP;
END $$;
