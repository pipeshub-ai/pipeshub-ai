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
  },
  aiModels: '/services/aiModels',
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
};
