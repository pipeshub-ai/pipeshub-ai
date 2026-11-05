-- upkeep reads and writes nothing in identity: no privilege and no jobs_all policy on any of its tables
REVOKE ALL ON identity.orgs, identity.users, identity.user_groups, identity.user_group_members, identity.org_auth_config,
  identity.user_activities, identity.used_reset_links, identity.org_logos, identity.user_pictures, identity.slug_counters, identity.user_credentials FROM pipeshub_jobs;
REVOKE USAGE ON SCHEMA identity FROM pipeshub_jobs;

DO $$ DECLARE r kernel.rls_registry; BEGIN
  FOR r IN SELECT * FROM kernel.rls_registry WHERE rel::text LIKE 'identity.%' AND jobs_access
  LOOP PERFORM kernel.apply_rls(r.rel, r.class, false, r.vetted_read, '5s', r.vetted_write); END LOOP;
END $$;
