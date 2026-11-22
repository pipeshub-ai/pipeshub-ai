#!/bin/bash
# Creates what is missing of the database's role layout and refreshes the file PgBouncer authenticates against. Idempotent: an
# existing role is never altered (a changed password needs ALTER ROLE, see README.md), so it runs at the first initialisation (from
# initdb/10-roles.sh) and again on every start (from pg-entrypoint.sh), which repairs a volume left half-set-up.
# Tables, policies and grants on them come with the migrations; this creates no privileges beyond the role layout.
#
# The server never sees a clear-text password: the SCRAM verifiers are computed here (scram-verifier.pl, password read from the
# environment, not argv) and sent in their place, and this session turns off statement tracking and statement logging.
set -euo pipefail
user="${POSTGRES_USER:-postgres}"; db="${POSTGRES_DB:-pipeshub}"
auth_dir="${PG_AUTH_DIR:-/pgbouncer-auth}"
verifier() { perl "${PG_SCRAM_VERIFIER:-/usr/local/bin/scram-verifier.pl}" "$1"; }

# Each verifier is computed on its own and checked, before anything is sent to the server: one that fails or comes out empty stops the
# whole script, so no role is ever created with an empty password.
args=(-v db="$db")
for entry in node:PG_NODE_PASSWORD conn:PG_CONNECTORS_PASSWORD idx:PG_INDEXING_PASSWORD qry:PG_QUERY_PASSWORD jobs:PG_JOBS_PASSWORD \
             mig:PG_MIGRATE_PASSWORD bak:PG_BACKUP_PASSWORD kv:PG_KV_PASSWORD; do
  name="${entry%%:*}"; var="${entry#*:}"
  value="$(verifier "$var")" || { echo "pg-roles: could not compute the verifier of $var; no role was created or changed" >&2; exit 1; }
  [ -n "$value" ] || { echo "pg-roles: the verifier of $var is empty; no role was created or changed" >&2; exit 1; }
  args+=(-v "$name=$value")
done
psql -v ON_ERROR_STOP=1 --username "$user" --dbname "$db" "${args[@]}" <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SET pg_stat_statements.track = 'none';
-- no role below has SUPERUSER, CREATEROLE or BYPASSRLS
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY[
    'pipeshub_owner',      -- owns every relation
    'pipeshub_app',        -- policy group; never holds table privileges
    'pipeshub_node', 'pipeshub_connectors', 'pipeshub_indexing', 'pipeshub_query',
    'pipeshub_kv',         -- key-value table only: the processes that just read configuration
    'pipeshub_restore', 'pipeshub_import',
    'pipeshub_vetted_kernel', 'pipeshub_vetted_identity', 'pipeshub_vetted_platform', 'pipeshub_vetted_retrieval',
    'pipeshub_vetted_graph', 'pipeshub_vetted_kg'   -- owners of SECURITY DEFINER functions
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN EXECUTE format('CREATE ROLE %I NOLOGIN', r); END IF;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_ro') THEN
    CREATE ROLE pipeshub_ro LOGIN;   -- no password: unusable until an operator sets one
  END IF;
END $$;
-- one login per service, a member of its own role only; it holds no privileges until the unit of work switches role, which the
-- PgBouncer reset undoes between clients
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', r.n, r.v)
  FROM (VALUES ('pipeshub_migrate', :'mig'), ('pipeshub_jobs', :'jobs'), ('pipeshub_backup', :'bak'),
               ('pipeshub_node_login', :'node'), ('pipeshub_connectors_login', :'conn'),
               ('pipeshub_indexing_login', :'idx'), ('pipeshub_query_login', :'qry'), ('pipeshub_kv_login', :'kv')) AS r(n, v)
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.n) \gexec
GRANT pipeshub_owner TO pipeshub_migrate;
GRANT pipeshub_app TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv
  WITH INHERIT TRUE, SET FALSE;                                      -- service roles inherit the policy group but cannot become it
GRANT pipeshub_app TO pipeshub_ro WITH INHERIT TRUE, SET FALSE;
GRANT pipeshub_node TO pipeshub_node_login WITH INHERIT FALSE, SET TRUE;
GRANT pipeshub_connectors TO pipeshub_connectors_login WITH INHERIT FALSE, SET TRUE;
GRANT pipeshub_indexing TO pipeshub_indexing_login WITH INHERIT FALSE, SET TRUE;
GRANT pipeshub_query TO pipeshub_query_login WITH INHERIT FALSE, SET TRUE;
GRANT pipeshub_kv TO pipeshub_kv_login WITH INHERIT FALSE, SET TRUE;
-- the migration owner may only hand the vetted roles out (ADMIN), never use them
GRANT pipeshub_vetted_kernel, pipeshub_vetted_identity, pipeshub_vetted_platform, pipeshub_vetted_retrieval,
      pipeshub_vetted_graph, pipeshub_vetted_kg TO pipeshub_owner
  WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
-- extensions are installed here, by the superuser, into schema ext (a migration cannot create an extension that is not trusted)
CREATE SCHEMA IF NOT EXISTS ext AUTHORIZATION pipeshub_owner;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA ext;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA ext;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE ON DATABASE :"db" TO pipeshub_owner;
REVOKE TEMPORARY ON DATABASE :"db" FROM PUBLIC;
-- server-side large objects have no org; nobody but a superuser may call them
DO $$ DECLARE f regprocedure; BEGIN
  FOR f IN SELECT p.oid::regprocedure FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace
           AND p.proname ~ '^(lo_|lo(read|write)$)' LOOP
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
  END LOOP; END $$;
SQL

# Verifier file for PgBouncer: the poolable logins only (migrate and backup connect to postgres directly). Rewritten on every run, so a
# lost or recreated pgbouncer_auth volume is repaired by the next start. It is written last and only when everything above succeeded,
# so it doubles as the success marker the healthchecks of the server and the pooler require (pg-entrypoint.sh removes the old one at start).
umask 077
psql -v ON_ERROR_STOP=1 --username "$user" --dbname "$db" -At -c \
  "SELECT format('\"%s\" \"%s\"', rolname, rolpassword) FROM pg_authid
   WHERE rolname IN ('pipeshub_node_login','pipeshub_connectors_login','pipeshub_indexing_login',
                     'pipeshub_query_login','pipeshub_kv_login','pipeshub_jobs') ORDER BY 1" > "$auth_dir/userlist.txt.new"
mv "$auth_dir/userlist.txt.new" "$auth_dir/userlist.txt"
