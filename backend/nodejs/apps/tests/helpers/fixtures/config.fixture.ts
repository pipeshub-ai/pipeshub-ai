export function createMockAppConfig(): Record<string, any> {
  return {
    jwtSecret: 'test-jwt-secret-key-for-unit-tests-32chars!',
    scopedJwtSecret: 'test-scoped-jwt-secret-for-tests-32chars!',
    cookieSecret: 'test-cookie-secret',
    rsAvailable: 'false',

    communicationBackend: 'http://localhost:3001',
    frontendUrl: 'http://localhost:3000',
    iamBackend: 'http://localhost:3001',
    authBackend: 'http://localhost:3001',
    cmBackend: 'http://localhost:3001',
    kbBackend: 'http://localhost:3001',
    esBackend: 'http://localhost:3001',
    storageBackend: 'http://localhost:3001',
    tokenBackend: 'http://localhost:3001',
    aiBackend: 'http://localhost:8000',
    connectorBackend: 'http://localhost:8088',
    connectorPublicUrl: 'http://localhost:8088',
    indexingBackend: 'http://localhost:8091',

    kafka: {
      brokers: ['localhost:9092'],
      ssl: false,
      sasl: undefined,
    },

    redis: {
      host: 'localhost',
      port: 6379,
      username: undefined,
      password: undefined,
      tls: false,
      db: 0,
    },

    mongo: {
      uri: 'mongodb://localhost:27017',
      db: 'test-db',
    },

    vectorDbType: 'qdrant',
    qdrant: {
      port: 6333,
      apiKey: 'test-qdrant-key',
      host: 'localhost',
      grpcPort: 6334,
    },

    arango: {
      url: 'http://localhost:8529',
      db: 'test-db',
      username: 'root',
      password: 'test-password',
    },

    etcd: {
      host: 'localhost',
      port: 2379,
      dialTimeout: 5000,
    },

    smtp: {
      host: 'localhost',
      port: 587,
      username: 'test@example.com',
      password: 'test-password',
      fromEmail: 'noreply@example.com',
    },

    storage: {
      storageType: 'local',
      endpoint: 'http://localhost:3001',
    },

    oauthIssuer: 'http://localhost:3001',
    oauthBackendUrl: 'http://localhost:3001',
    mcpScopes: ['read', 'write'],

    skipDomainCheck: true,

    maxRequestsPerMinute: 100,
    maxOAuthClientRequestsPerMinute: 50,
  };
}
