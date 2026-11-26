-- The agent harness keeps keys in the key-value store that platform_0006's matrix (written before the harness merged) does not name.
-- The Python processes that write them connect as the connectors role (the connectors service, and the harness worker and model gateway,
-- which take the connectors login: process_monitor.sh) or as the query role:
--   /services/agentHarness/credentials/ and /services/agentHarness/credentialKeys  the credential vault (accounts, toolset and MCP
--       credentials it holds) and its key ring, created by the first process that opens the vault: query's routes and agents, the
--       connectors service's token refresh, the harness worker
--   /services/controlPlane/credentials/  control-plane binding documents, written back on a token refresh (connectors service, query's
--       on-demand MCP refresh)
--   /services/agentHarness/repositoryCache  the harness worker's repository cache status (connectors role)
--   /services/harness/revokedReleaseKeys  the last good copy of the signed release-key revocation list the harness worker fetches
--       (connectors role)
-- Reads need nothing new: connectors and query read every key but the Node API's own. The Node API writes every other harness and
-- control-plane key. On top of indexing's and kv's allow-lists, the guard now also refuses the harness's credentials, key ring, role
-- and worker credentials, sandbox-plane component documents and control-plane credentials, which no indexing or configuration-only
-- service reads (backend/db/tests/test_kv_access_map.py).
-- This replaces platform.kv_may with platform_0011's definition (platform_0010's plus the sandbox alert claims) plus those write prefixes, and platform.kv_admin_or_credential with
-- platform_0006's plus those keys; nothing else changes.

CREATE OR REPLACE FUNCTION platform.kv_admin_or_credential(p_key text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT p_key IN ('/services/smtp', '/services/slackBot', '/services/webSearch') OR pg_catalog.starts_with(p_key, '/services/auth/')
    OR pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/') OR pg_catalog.starts_with(p_key, 'crawl:')
    OR (pg_catalog.starts_with(p_key, '/services/connectors/') AND NOT p_key OPERATOR(pg_catalog.~) '^/services/connectors/[^/]+/metadataSchemas/')
    OR pg_catalog.starts_with(p_key, '/services/oauth/') OR pg_catalog.starts_with(p_key, '/services/oauths/')
    OR pg_catalog.starts_with(p_key, '/services/toolset') OR pg_catalog.starts_with(p_key, '/services/api-toolsets/')
    OR pg_catalog.starts_with(p_key, '/services/mcp/') OR pg_catalog.starts_with(p_key, '/services/agents/')
    OR pg_catalog.starts_with(p_key, '/services/agentHarness/credentials/') OR p_key = '/services/agentHarness/credentialKeys'
    OR pg_catalog.starts_with(p_key, '/services/agentHarness/roleCredentials/') OR p_key = '/services/agentHarness/workerCredential'
    OR pg_catalog.starts_with(p_key, '/services/agentHarness/components/') OR pg_catalog.starts_with(p_key, '/services/controlPlane/credentials/') $$;

CREATE OR REPLACE FUNCTION platform.kv_may(p_key text, p_write boolean) RETURNS boolean
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
      OR pg_catalog.starts_with(p_key, '/services/vectordb/embedding_epoch/')
      OR pg_catalog.starts_with(p_key, '/migrations/') OR pg_catalog.starts_with(p_key, 'state:')
      OR pg_catalog.starts_with(p_key, '/services/agentHarness/credentials/') OR p_key = '/services/agentHarness/credentialKeys'
      OR pg_catalog.starts_with(p_key, '/services/controlPlane/credentials/')
      OR p_key IN ('/services/agentHarness/repositoryCache', '/services/harness/revokedReleaseKeys')
    ELSE NOT (p_key IN ('/services/smtp', '/services/slackBot') OR pg_catalog.starts_with(p_key, '/services/auth/')
              OR pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/') OR pg_catalog.starts_with(p_key, 'crawl:')) END
  WHEN 'pipeshub_query' THEN CASE WHEN p_write THEN
      p_key = '/services/toolset-instances' OR pg_catalog.starts_with(p_key, '/services/toolsets/') OR pg_catalog.starts_with(p_key, '/services/oauths/toolsets/')
      OR pg_catalog.starts_with(p_key, '/services/toolset-user-instances/') OR pg_catalog.starts_with(p_key, '/services/toolset-personal-owners/')
      OR pg_catalog.starts_with(p_key, '/services/toolset-tool-rules/') OR pg_catalog.starts_with(p_key, '/services/api-toolsets/')
      OR (pg_catalog.starts_with(p_key, '/services/mcp/') AND NOT pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/'))
      OR pg_catalog.starts_with(p_key, '/services/agents/') OR p_key = '/services/aiModelApiModes'
      OR pg_catalog.starts_with(p_key, '/services/scopeFields/') OR pg_catalog.starts_with(p_key, '/services/vectordb/') OR pg_catalog.starts_with(p_key, 'state:')
      OR pg_catalog.starts_with(p_key, '/services/sandbox/alerts/')
      OR pg_catalog.starts_with(p_key, '/services/agentHarness/credentials/') OR p_key = '/services/agentHarness/credentialKeys'
      OR pg_catalog.starts_with(p_key, '/services/controlPlane/credentials/')
    ELSE NOT (p_key IN ('/services/smtp', '/services/slackBot') OR pg_catalog.starts_with(p_key, '/services/auth/')
              OR pg_catalog.starts_with(p_key, '/services/mcp/accessPolicy/') OR pg_catalog.starts_with(p_key, 'crawl:')) END
  WHEN 'pipeshub_indexing' THEN NOT platform.kv_admin_or_credential(p_key) AND (
      pg_catalog.starts_with(p_key, 'state:') OR pg_catalog.starts_with(p_key, '/services/scopeFields/') OR pg_catalog.starts_with(p_key, '/services/vectordb/')
      OR pg_catalog.starts_with(p_key, '/services/platform/artifactRollout/membership/') OR pg_catalog.starts_with(p_key, '/services/entityCleanup/')
      OR pg_catalog.starts_with(p_key, '/migrations/class_units_backfill/') OR p_key = '/services/indexing/spendGuard/hmacKey'
      OR (NOT p_write AND (
        p_key IN ('/services/secretKeys', '/services/endpoints', '/services/storage', '/services/arangodb', '/services/kafka', '/services/redis',
                  '/services/qdrant', '/services/pgvector', '/services/opensearch', '/services/redis-vector', '/services/aiModels',
                  '/services/aiGateway/identityPolicy', '/services/namedEntities', '/services/metricsCollection', '/services/platform/settings',
                  '/services/file_types/config', '/migrations/kh_listing_state_v1', '/migrations/etcd_to_redis', '/services/indexing/spendGuard',
                  '/services/indexing/parseAdmission')
        OR p_key OPERATOR(pg_catalog.~) '^/services/connectors/[^/]+/metadataSchemas/')))
  WHEN 'pipeshub_kv' THEN NOT platform.kv_admin_or_credential(p_key) AND (p_key = '/services/indexing/spendGuard/hmacKey' OR (NOT p_write AND p_key IN (
      '/services/aiModels', '/services/secretKeys', '/services/aiGateway/identityPolicy', '/services/metricsCollection', '/migrations/etcd_to_redis',
      '/services/redis')))
  ELSE false END $$;
