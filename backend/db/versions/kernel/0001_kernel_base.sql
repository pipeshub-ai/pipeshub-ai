-- Roles the schema is built on. Normally created by the deployment bootstrap; created here only when
-- missing (a bare database). LOGIN roles and passwords are never created by migrations.
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['pipeshub_owner', 'pipeshub_app', 'pipeshub_node', 'pipeshub_connectors',
                           'pipeshub_indexing', 'pipeshub_query'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
      IF r IN ('pipeshub_node', 'pipeshub_connectors', 'pipeshub_indexing', 'pipeshub_query') THEN
        EXECUTE format('GRANT pipeshub_app TO %I WITH INHERIT TRUE, SET FALSE', r);
      END IF;
    END IF;
  END LOOP;
END $$;

DO $$ BEGIN EXECUTE format('GRANT CREATE ON DATABASE %I TO pipeshub_owner', current_database()); END $$;

-- Everything below is owned by the owner role, whoever runs the migration.
CREATE SCHEMA IF NOT EXISTS kernel;
ALTER SCHEMA kernel OWNER TO pipeshub_owner;
DO $$ BEGIN
  IF to_regclass('kernel.alembic_version') IS NOT NULL THEN
    ALTER TABLE kernel.alembic_version OWNER TO pipeshub_owner;
  END IF;
END $$;

-- TEMP is granted only to the writers that COPY. Takes effect only when the migrating role owns the database (or is a superuser);
-- otherwise the deployment bootstrap has to do it (PostgreSQL only warns here).
DO $$ BEGIN EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()); END $$;

SET LOCAL ROLE pipeshub_owner;

-- Extensions live in schema ext so function search_path pinning stays "pg_catalog, <schema>, ext, pg_temp".
CREATE SCHEMA IF NOT EXISTS ext;
GRANT USAGE ON SCHEMA kernel, ext TO pipeshub_app;

-- Schema epochs: written by the migration environment from revision ids, never by a revision.
-- GREATEST: an epoch and a min_code_epoch never go back.
CREATE TABLE kernel.schema_epoch (
  branch         text PRIMARY KEY,
  epoch          int NOT NULL,
  min_code_epoch int NOT NULL DEFAULT 0,
  updated_at     timestamptz NOT NULL DEFAULT now());

CREATE FUNCTION kernel.set_epoch(p_branch text, p_epoch int, p_min_code int DEFAULT 0) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, kernel, pg_temp AS $$
  INSERT INTO kernel.schema_epoch AS e (branch, epoch, min_code_epoch) VALUES (p_branch, p_epoch, p_min_code)
  ON CONFLICT (branch) DO UPDATE SET epoch = GREATEST(e.epoch, excluded.epoch),
    min_code_epoch = GREATEST(e.min_code_epoch, excluded.min_code_epoch), updated_at = now() $$;

-- A contract revision whose expand step needed a backfill starts with kernel.require_job_done('<job>').
CREATE TABLE kernel.completed_jobs (name text PRIMARY KEY, finished_at timestamptz NOT NULL DEFAULT now());

CREATE FUNCTION kernel.require_job_done(p_name text) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, kernel, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM kernel.completed_jobs WHERE name = p_name) THEN
    RAISE EXCEPTION 'backfill job "%" has not completed', p_name USING ERRCODE = '55000';
  END IF;
END $$;

-- Capability guards for revisions: version-dependent or extension-dependent DDL is guarded in SQL.
CREATE FUNCTION kernel.pg_major() RETURNS int
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$ SELECT current_setting('server_version_num')::int / 10000 $$;

CREATE FUNCTION kernel.has_extension(p_name text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$ SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = p_name) $$;

-- Explicit grants only. Writers of these tables are the migration role and, later, the jobs role.
REVOKE ALL ON FUNCTION kernel.set_epoch(text, int, int), kernel.require_job_done(text),
  kernel.pg_major(), kernel.has_extension(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel.pg_major(), kernel.has_extension(text) TO pipeshub_app;
GRANT SELECT ON kernel.schema_epoch TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
