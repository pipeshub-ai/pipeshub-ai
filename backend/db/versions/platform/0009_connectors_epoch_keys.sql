-- The search-index cleanup runs in the connectors service: before it resets anything it makes a new epoch current for each records
-- collection (CollectionEpochs.begin_reset), which creates the collection's current-epoch pointer and the issuing and live keys of
-- the new token under /services/vectordb/embedding_epoch/. Under enforce the connectors role could not write them, so the cleanup failed.
-- This replaces platform.kv_may with platform_0007's definition plus that one write prefix for the connectors role; nothing else changes
-- (the confirmation of the drop and the precision pins are written by the indexing and query services, which already have /services/vectordb/;
-- the connectors role still writes no pin key and no other /services/vectordb/ key). CREATE OR REPLACE keeps the owner, the privileges
-- and the policies that call it.
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
                  '/services/file_types/config', '/migrations/kh_listing_state_v1', '/migrations/etcd_to_redis', '/services/indexing/spendGuard')
        OR p_key OPERATOR(pg_catalog.~) '^/services/connectors/[^/]+/metadataSchemas/')))
  WHEN 'pipeshub_kv' THEN NOT platform.kv_admin_or_credential(p_key) AND (p_key = '/services/indexing/spendGuard/hmacKey' OR (NOT p_write AND p_key IN (
      '/services/aiModels', '/services/secretKeys', '/services/aiGateway/identityPolicy', '/services/metricsCollection', '/migrations/etcd_to_redis',
      '/services/redis')))
  ELSE false END $$;
