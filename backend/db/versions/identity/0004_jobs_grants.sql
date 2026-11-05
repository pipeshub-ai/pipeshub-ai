-- the jobs role keeps only what the organisation gauges read: three tables, SELECT
REVOKE ALL ON identity.orgs, identity.users, identity.user_groups, identity.user_group_members, identity.org_auth_config,
  identity.user_activities, identity.used_reset_links, identity.org_logos, identity.user_pictures, identity.slug_counters, identity.user_credentials FROM pipeshub_jobs;
GRANT SELECT ON identity.users, identity.orgs, identity.org_auth_config TO pipeshub_jobs;      -- table level: the kernel refuses column privileges (check 38)

-- the jobs_all policy (FOR ALL, bounded by the grants above) stays on those three tables only
DO $$ DECLARE r kernel.rls_registry; BEGIN
  FOR r IN SELECT * FROM kernel.rls_registry WHERE rel::text LIKE 'identity.%' AND jobs_access
           AND rel::text NOT IN ('identity.users', 'identity.orgs', 'identity.org_auth_config')
  LOOP PERFORM kernel.apply_rls(r.rel, r.class, false, r.vetted_read, '5s', r.vetted_write); END LOOP;
END $$;
