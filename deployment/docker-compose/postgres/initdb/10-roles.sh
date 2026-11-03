#!/bin/bash
# First start only (docker-entrypoint-initdb.d): create the database's login roles and the role graph they
# sit in, then write the SCRAM verifiers PgBouncer authenticates against. Tables, policies and grants are
# created later by the migrations; this script creates no privileges beyond the schema-less role layout.
set -euo pipefail

for v in PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD \
         PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD; do
  if [ -z "${!v:-}" ]; then echo "initdb: $v is empty; set it in .env (install.sh generates it)" >&2; exit 1; fi
done

# Passwords reach SQL as psql variables (:'name'), never spliced into the statement text.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v node="$PG_NODE_PASSWORD" -v conn="$PG_CONNECTORS_PASSWORD" -v idx="$PG_INDEXING_PASSWORD" \
  -v qry="$PG_QUERY_PASSWORD" -v jobs="$PG_JOBS_PASSWORD" -v mig="$PG_MIGRATE_PASSWORD" \
  -v bak="$PG_BACKUP_PASSWORD" <<'SQL'
SET password_encryption = 'scram-sha-256';
-- no role below has SUPERUSER, CREATEROLE or BYPASSRLS
CREATE ROLE pipeshub_owner NOLOGIN;                                  -- owns every relation
CREATE ROLE pipeshub_migrate LOGIN PASSWORD :'mig';                  -- migrations and partition maintenance
GRANT pipeshub_owner TO pipeshub_migrate;
CREATE ROLE pipeshub_app NOLOGIN;                                    -- policy group; never holds table privileges
-- service roles inherit the policy group but cannot become it
CREATE ROLE pipeshub_node NOLOGIN;       CREATE ROLE pipeshub_connectors NOLOGIN;
CREATE ROLE pipeshub_indexing NOLOGIN;   CREATE ROLE pipeshub_query NOLOGIN;
CREATE ROLE pipeshub_kv NOLOGIN;                                     -- key-value table only: the processes that just read configuration
GRANT pipeshub_app TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv
  WITH INHERIT TRUE, SET FALSE;
-- one login per service, a member of its own role only; it holds no privileges until the unit of work
-- switches role, which the PgBouncer reset undoes between clients
CREATE ROLE pipeshub_node_login LOGIN PASSWORD :'node';
CREATE ROLE pipeshub_connectors_login LOGIN PASSWORD :'conn';
CREATE ROLE pipeshub_indexing_login LOGIN PASSWORD :'idx';
CREATE ROLE pipeshub_query_login LOGIN PASSWORD :'qry';
GRANT pipeshub_node TO pipeshub_node_login WITH INHERIT FALSE, SET TRUE;
GRANT pipeshub_connectors TO pipeshub_connectors_login WITH INHERIT FALSE, SET TRUE;
GRANT pipeshub_indexing TO pipeshub_indexing_login WITH INHERIT FALSE, SET TRUE;
GRANT pipeshub_query TO pipeshub_query_login WITH INHERIT FALSE, SET TRUE;
CREATE ROLE pipeshub_jobs LOGIN PASSWORD :'jobs';
CREATE ROLE pipeshub_ro LOGIN;                                       -- no password: unusable until an operator sets one
GRANT pipeshub_app TO pipeshub_ro WITH INHERIT TRUE, SET FALSE;
CREATE ROLE pipeshub_backup LOGIN PASSWORD :'bak';                   -- read-only; grants come with the migrations
CREATE ROLE pipeshub_restore NOLOGIN;                                -- enabled only inside a restore window
CREATE ROLE pipeshub_import NOLOGIN;                                 -- the one-off copy of an existing install; the move script enables the login while it runs
-- owners of the SECURITY DEFINER functions in schemas kernel, identity and platform (db.bootstrap_roles adds the ones a later schema needs); the migration owner may only hand them out (ADMIN), never use them
CREATE ROLE pipeshub_vetted_kernel NOLOGIN;
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
CREATE ROLE pipeshub_vetted_identity NOLOGIN;                        -- people, organisations and sign-in state
GRANT pipeshub_vetted_identity TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
CREATE ROLE pipeshub_vetted_platform NOLOGIN;                        -- background-job state
GRANT pipeshub_vetted_platform TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
-- extensions are installed here, by the superuser, into schema ext (a migration cannot: pgvector is not a trusted extension)
CREATE SCHEMA ext AUTHORIZATION pipeshub_owner;
CREATE EXTENSION vector WITH SCHEMA ext;
CREATE EXTENSION pg_trgm WITH SCHEMA ext;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE ON DATABASE pipeshub TO pipeshub_owner;
REVOKE TEMPORARY ON DATABASE pipeshub FROM PUBLIC;                   -- needs the database owner; the migrations cannot do it
-- server-side large objects have no org; nobody but a superuser may call them
DO $$ DECLARE f regprocedure; BEGIN
  FOR f IN SELECT p.oid::regprocedure FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace
           AND p.proname ~ '^(lo_|lo(read|write)$)' LOOP
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
  END LOOP; END $$;
SQL

# The login of the configuration-only role is optional: without PG_KV_PASSWORD those processes run as `query`.
if [ -n "${PG_KV_PASSWORD:-}" ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v kv="$PG_KV_PASSWORD" <<'SQL'
SET password_encryption = 'scram-sha-256';
CREATE ROLE pipeshub_kv_login LOGIN PASSWORD :'kv';
GRANT pipeshub_kv TO pipeshub_kv_login WITH INHERIT FALSE, SET TRUE;
SQL
fi

# Verifier file for PgBouncer: the poolable logins only (migrate and backup connect to postgres directly).
umask 077
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -At -c \
  "SELECT format('\"%s\" \"%s\"', rolname, rolpassword) FROM pg_authid
   WHERE rolname IN ('pipeshub_node_login','pipeshub_connectors_login','pipeshub_indexing_login',
                     'pipeshub_query_login','pipeshub_kv_login','pipeshub_jobs') ORDER BY 1" > /pgbouncer-auth/userlist.txt
