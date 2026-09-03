export const configPaths = {
  secretKeys: '/services/secretKeys',
  metricsCollection: '/services/metricsCollection',
  storageService: '/services/storage',
  connectors: {
    googleWorkspace: {
      base: '/services/connectors/googleWorkspace/',
      credentials: {
        individual:
          '/services/connectors/googleWorkspace/credentials/individual',
        business: '/services/connectors/googleWorkspace/credentials/business',
      },
      config: '/services/connectors/googleWorkspace/oauth/config',
    },
    atlassian: {
      base: '/services/connectors/atlassian/',
      credentials: '/services/connectors/atlassian/credentials',
      config: '/services/connectors/atlassian/config',
    },
    onedrive: {
      base: '/services/connectors/onedrive/',
      config: '/services/connectors/onedrive/config',
    },
    sharepoint: {
      base: '/services/connectors/sharepoint/',
      config: '/services/connectors/sharepoint/config',
    },
  },
  smtp: '/services/smtp',
  auth: {
    base: '/services/auth',
    azureAD: '/services/auth/azureAd',
    google: '/services/auth/google',
    okta: '/services/auth/okta',
    microsoft: '/services/auth/microsoft',
    sso: '/services/auth/sso',
    oauth: '/services/auth/oauth',
    github: '/services/auth/github',
  },
  aiModels: '/services/aiModels',
  aiModelsEmbedding: '/services/aiModels/embedding',
  systemPrompts: '/services/systemPrompts',
  // v2 re-runs the same backfill after the BullMQ queue prefix moved from the
  // default `bull` to the hash-tagged `{crawling}` (needed so BullMQ's
  // multi-key Lua scripts stay in one Redis Cluster slot). Jobs written under
  // the old prefix are invisible to both the queue and the worker, so the
  // backfill has to re-create every schedule from its persisted `sync` block.
  connectorSyncScheduledJobsMigrationV2:
    '/migrations/connector_sync_scheduled_jobs_v2',
  chatKbFiltersMigration: '/migrations/chat_kb_filters_v1',
  adminRoleMigration: '/migrations/admin_role_v1',
  documentOrgIdMigration: '/migrations/document_orgid_v1',
  // Python-owned flag (backend/python/app/migrations/kb_apps_migration.py) — read-only from Node.
  kbAppsMigrationDone: '/migrations/kb_apps_v1',
  webSearch: '/services/webSearch',
  slackBot: '/services/slackBot',
  db: {
    mongodb: '/services/mongodb',
    arangodb: '/services/arangodb',
    qdrant: '/services/qdrant',
  },
  keyValueStore: {
    redis: '/services/redis',
  },
  broker: {
    kafka: '/services/kafka',
    redisStreams: '/services/redis-streams',
    messageBroker: '/services/message-broker',
  },
  aiBackend: '/services/query',
  endpoint: '/services/endpoints',
  url: {
    auth: '/services/nodejs/auth',
    storage: 'services/nodejs/storage',
    communication: '/services/nodejs/communication',
    iam: '/services/nodejs/iam',
    kb: '/services/nodejs/kb',
    es: '/services/nodejs/es',
    cm: '/services/nodejs/cm',
    frontend: '/services/frontend',
    indexing: '/services/indexing',
    connector: '/services/connector',
    query: '/services/query',
  },
  platform: {
    settings: '/services/platform/settings',
  },
  deployment: '/services/deployment',
  inheritance: '/services/inheritance',
};
