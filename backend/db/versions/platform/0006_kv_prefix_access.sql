-- Key-value access by key prefix: each service role reads and writes only the parts of platform.kv_entries it uses, so one service's
-- login no longer reads or rewrites every key (the signing keys, the store connection settings and the admin-only settings).
--   node        every key (the admin API writes the settings, the signing keys and the store connection settings)
--   connectors  reads every key but the admin API's own (mail, chat bot, sign-in providers, MCP access policy, crawl marks); writes the
--               connector, OAuth app, toolset, API toolset and MCP keys, its background jobs, scope fields, demo data, the deployment
--               record, migration flags and runtime state
--   query       reads as connectors; writes the toolset, API toolset and MCP keys, parked agent turns, the model API modes, scope fields,
--               the vector collection settings and runtime state
--   indexing    reads only the keys its code reads (signing keys, endpoints, storage, store connection settings, AI models, the AI gateway
--               identity policy, named-entity, telemetry, platform and file-type settings, two migration flags, connector metadata schemas); writes
--               runtime state, scope fields, the vector collection settings, artifact rollout membership, entity cleanup intents and its
--               backfill flags
--   kv          reads the AI model settings, the signing keys, the AI gateway identity policy, the telemetry settings and one migration
--               flag; writes nothing
-- Indexing's and kv's lists are allow-lists checked against the code (backend/db/tests/test_kv_access_map.py: every key their services
-- reach is listed with its reader or with why it is not read, and the matrix grants exactly the listed keys), and on top of them
-- platform.kv_admin_or_credential is refused whatever the list says.
-- Mode (platform.kv_access, set by db.migrate from PG_KV_ACCESS): shadow (the default) admits everything and writes each access outside
-- a role's prefixes to the server log; enforce refuses it: such a row is not visible, and inserting or upserting it fails with 42501.
-- The table owner (migrations) is not subject to the policies (row-level security is enabled, not forced).

CREATE TABLE platform.kv_access (
  id         boolean PRIMARY KEY DEFAULT true CHECK (id),
  enforce    boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now());
INSERT INTO platform.kv_access DEFAULT VALUES;
SELECT kernel.register_internal('platform.kv_access');
GRANT SELECT ON platform.kv_access TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv;

-- A guard on top of indexing's and kv's allow-lists (never a grant): the Node API's own settings (mail, chat bot, sign-in providers, MCP
-- access policy, crawl marks), and the credentials of connectors, OAuth apps, toolsets, API toolsets, MCP servers and web search, and
-- parked agent turns, which only connectors and query use. A connector's metadata schemas (indexing reads them) are not credentials.
CREATE FUNCTION platform.kv_admin_or_credential(p_key text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT p_key IN ('/services/smtp', '/services/slackBot', '/services/webSearch') OR pg_catalog.starts_with(p_key, '/services/auth/')
    OR pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/') OR pg_catalog.starts_with(p_key, 'crawl:')
    OR (pg_catalog.starts_with(p_key, '/services/connectors/') AND NOT p_key OPERATOR(pg_catalog.~) '^/services/connectors/[^/]+/metadataSchemas/')
    OR pg_catalog.starts_with(p_key, '/services/oauth/') OR pg_catalog.starts_with(p_key, '/services/oauths/')
    OR pg_catalog.starts_with(p_key, '/services/toolset') OR pg_catalog.starts_with(p_key, '/services/api-toolsets/')
    OR pg_catalog.starts_with(p_key, '/services/mcp/') OR pg_catalog.starts_with(p_key, '/services/agents/') $$;

-- The matrix. No SET clause, so the planner inlines it into the policies; its functions are schema-qualified, and the service roles
-- can create nothing in any schema, so a caller's search_path cannot change what it calls. (Not a SQL-standard body: the security
-- surface hashes prosrc, which such a body leaves empty.)
CREATE FUNCTION platform.kv_may(p_key text, p_write boolean) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
SELECT CASE current_user
  WHEN 'pipeshub_node' THEN true
  WHEN 'pipeshub_connectors' THEN CASE WHEN p_write THEN
      pg_catalog.starts_with(p_key, '/services/connectors/') OR pg_catalog.starts_with(p_key, '/services/oauth/')
      OR p_key = '/services/toolset-instances' OR pg_catalog.starts_with(p_key, '/services/toolsets/') OR pg_catalog.starts_with(p_key, '/services/oauths/toolsets/')
      OR pg_catalog.starts_with(p_key, '/services/toolset-user-instances/') OR pg_catalog.starts_with(p_key, '/services/toolset-personal-owners/')
      OR pg_catalog.starts_with(p_key, '/services/toolset-tool-rules/') OR pg_catalog.starts_with(p_key, '/services/api-toolsets/')
      OR (pg_catalog.starts_with(p_key, '/services/mcp/') AND NOT pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/'))
      OR pg_catalog.starts_with(p_key, '/services/softDeletePurge/') OR pg_catalog.starts_with(p_key, '/services/storageRelease/')
      OR pg_catalog.starts_with(p_key, '/services/lateWriteSweep/') OR pg_catalog.starts_with(p_key, '/services/storageMigration/')
      OR pg_catalog.starts_with(p_key, '/services/entityCleanup/') OR pg_catalog.starts_with(p_key, '/services/scopeFields/')
      OR pg_catalog.starts_with(p_key, '/services/demoData/') OR p_key = '/services/deployment'
      OR pg_catalog.starts_with(p_key, '/migrations/') OR pg_catalog.starts_with(p_key, 'state:')
    ELSE NOT (p_key IN ('/services/smtp', '/services/slackBot') OR pg_catalog.starts_with(p_key, '/services/auth/')
              OR pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/') OR pg_catalog.starts_with(p_key, 'crawl:')) END
  WHEN 'pipeshub_query' THEN CASE WHEN p_write THEN
      p_key = '/services/toolset-instances' OR pg_catalog.starts_with(p_key, '/services/toolsets/') OR pg_catalog.starts_with(p_key, '/services/oauths/toolsets/')
      OR pg_catalog.starts_with(p_key, '/services/toolset-user-instances/') OR pg_catalog.starts_with(p_key, '/services/toolset-personal-owners/')
      OR pg_catalog.starts_with(p_key, '/services/toolset-tool-rules/') OR pg_catalog.starts_with(p_key, '/services/api-toolsets/')
      OR (pg_catalog.starts_with(p_key, '/services/mcp/') AND NOT pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/'))
      OR pg_catalog.starts_with(p_key, '/services/agents/') OR p_key = '/services/aiModelApiModes'
      OR pg_catalog.starts_with(p_key, '/services/scopeFields/') OR pg_catalog.starts_with(p_key, '/services/vectordb/') OR pg_catalog.starts_with(p_key, 'state:')
    ELSE NOT (p_key IN ('/services/smtp', '/services/slackBot') OR pg_catalog.starts_with(p_key, '/services/auth/')
              OR pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/') OR pg_catalog.starts_with(p_key, 'crawl:')) END
  WHEN 'pipeshub_indexing' THEN NOT platform.kv_admin_or_credential(p_key) AND (
      pg_catalog.starts_with(p_key, 'state:') OR pg_catalog.starts_with(p_key, '/services/scopeFields/') OR pg_catalog.starts_with(p_key, '/services/vectordb/')
      OR pg_catalog.starts_with(p_key, '/services/platform/artifactRollout/membership/') OR pg_catalog.starts_with(p_key, '/services/entityCleanup/')
      OR pg_catalog.starts_with(p_key, '/migrations/class_units_backfill/')
      OR (NOT p_write AND (
        p_key IN ('/services/secretKeys', '/services/endpoints', '/services/storage', '/services/arangodb', '/services/kafka', '/services/redis',
                  '/services/qdrant', '/services/pgvector', '/services/opensearch', '/services/redis-vector', '/services/aiModels',
                  '/services/aiGateway/identityPolicy', '/services/namedEntities', '/services/metricsCollection', '/services/platform/settings',
                  '/services/file_types/config', '/migrations/kh_listing_state_v1', '/migrations/etcd_to_redis')
        OR p_key OPERATOR(pg_catalog.~) '^/services/connectors/[^/]+/metadataSchemas/')))
  WHEN 'pipeshub_kv' THEN NOT p_write AND NOT platform.kv_admin_or_credential(p_key) AND p_key IN (
      '/services/aiModels', '/services/secretKeys', '/services/aiGateway/identityPolicy', '/services/metricsCollection', '/migrations/etcd_to_redis')
  ELSE false END $$;

-- Outside the role's prefixes: in enforce, refused (false); in shadow, admitted (true) and written to the server log (level LOG: the
-- server log by default, never the client). The log names the role, the operation and at most the first three path segments (or two
-- colon-separated parts) of the key, never its value. A dead (expired) row is not logged: only the expired-row purge touches those.
CREATE FUNCTION platform.kv_outside(p_key text, p_op text, p_expires_at timestamptz) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (SELECT enforce FROM platform.kv_access) IS NOT false THEN
    RETURN false;
  END IF;
  IF p_expires_at IS NULL OR p_expires_at > now() THEN
    RAISE LOG 'pipeshub kv access outside the role''s prefixes (shadow): role %, %, key under %', current_user, p_op,
      CASE WHEN left(p_key, 1) = '/' THEN array_to_string((string_to_array(p_key, '/'))[1:4], '/')
           ELSE array_to_string((string_to_array(p_key, ':'))[1:2], ':') END;
  END IF;
  RETURN true;
END $$;

REVOKE ALL ON FUNCTION platform.kv_may(text, boolean), platform.kv_admin_or_credential(text), platform.kv_outside(text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.kv_may(text, boolean), platform.kv_admin_or_credential(text), platform.kv_outside(text, text, timestamptz)
  TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv;

ALTER TABLE platform.kv_entries ENABLE ROW LEVEL SECURITY;
CREATE POLICY kv_read ON platform.kv_entries FOR SELECT
  TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv
  USING (platform.kv_may(key, false) OR platform.kv_outside(key, 'read', expires_at));
CREATE POLICY kv_insert ON platform.kv_entries FOR INSERT
  TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv
  WITH CHECK (platform.kv_may(key, true) OR platform.kv_outside(key, 'insert', expires_at));
CREATE POLICY kv_update ON platform.kv_entries FOR UPDATE
  TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv
  USING (platform.kv_may(key, true) OR platform.kv_outside(key, 'update', expires_at))
  WITH CHECK (platform.kv_may(key, true) OR platform.kv_outside(key, 'update', expires_at));
-- a dead row may be deleted by any role that can read its key: each writer's purge (a DELETE over a SELECT of expired keys) reaches
-- only the expired rows that role can read, and the rest wait for the node role's purge, which reads every key
CREATE POLICY kv_delete ON platform.kv_entries FOR DELETE
  TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv
  USING (platform.kv_may(key, true) OR expires_at <= now() OR platform.kv_outside(key, 'delete', expires_at));

SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_read');
SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_insert');
SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_update');
SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_delete');
