-- the gauge functions' owner reads the tables: vetted read, with the jobs role's access unchanged
DO $$ DECLARE r kernel.rls_registry; BEGIN
  FOR r IN SELECT * FROM kernel.rls_registry WHERE rel = ANY (ARRAY['identity.users'::regclass, 'identity.orgs'::regclass, 'identity.org_auth_config'::regclass])
  LOOP PERFORM kernel.apply_rls(r.rel, r.class, true, true, '5s', r.vetted_write); END LOOP;
END $$;
