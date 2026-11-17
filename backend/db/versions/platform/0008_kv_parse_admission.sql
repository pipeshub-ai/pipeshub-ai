-- The class admission of parse work keeps its numbers in /services/indexing/parseAdmission, re-read at most every 30 s without a restart
-- (modules/parse_engine/admission.py: LiveAdmissionConfig.refresh). Only the indexing service reads it: indexing_main.py attaches the
-- configuration service to the admission wiring at start; the other services keep the wiring's store-less config and read no key.
-- The key was not in indexing's allow-list, so in enforce the read was refused and the numbers stayed at their environment defaults.
-- The matrix grants exactly this: the key, to read, for indexing.

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
