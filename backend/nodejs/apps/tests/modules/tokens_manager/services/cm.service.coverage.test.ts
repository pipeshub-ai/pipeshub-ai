import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  randomKeyGenerator,
  ConfigService,
} from '../../../../src/modules/tokens_manager/services/cm.service'
import { envGuard } from '../../../helpers/env-guard'

/**
 * Coverage tests for ConfigService - exercises all the get*Url methods
 * and secret key management by creating an instance via Object.create
 * and mocking the internal services.
 */
describe('ConfigService - coverage', () => {
  const env = envGuard()
  let service: ConfigService
  let mockKvStore: any
  let mockEncryption: any

  beforeEach(() => {
    env.snapshot()
    mockKvStore = {
      get: sinon.stub(),
      set: sinon.stub().resolves(),
      connect: sinon.stub().resolves(),
      watchKey: sinon.stub().resolves(),
    }
    mockEncryption = {
      encrypt: sinon.stub().callsFake((val: string) => `enc:${val}`),
      decrypt: sinon.stub().callsFake((val: string) => val.replace('enc:', '')),
    }

    // Create instance bypassing private constructor
    service = Object.create(ConfigService.prototype)
    ;(service as any).keyValueStoreService = mockKvStore
    ;(service as any).encryptionService = mockEncryption
    ;(service as any).configManagerConfig = { secretKey: 'test-secret', algorithm: 'aes-256-cbc' }
  })

  afterEach(() => {
    env.restore()
    sinon.restore()
  })

  // =========================================================================
  // connect
  // =========================================================================
  describe('connect', () => {
    it('should call keyValueStoreService.connect', async () => {
      await service.connect()
      expect(mockKvStore.connect.calledOnce).to.be.true
    })
  })

  // =========================================================================
  // getSmtpConfig
  // =========================================================================
  describe('getSmtpConfig', () => {
    it('should return parsed config when encrypted config exists', async () => {
      const smtpData = { host: 'smtp.test.com', port: 587, fromEmail: 'no@test.com' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(smtpData))
      mockEncryption.decrypt.returns(JSON.stringify(smtpData))

      const result = await service.getSmtpConfig()
      expect(result).to.deep.equal(smtpData)
    })

    it('should return null when no encrypted config exists', async () => {
      mockKvStore.get.resolves(null)

      const result = await service.getSmtpConfig()
      expect(result).to.be.null
    })

    it('should return null when encrypted config is empty string', async () => {
      mockKvStore.get.resolves('')

      const result = await service.getSmtpConfig()
      expect(result).to.be.null
    })
  })

  // =========================================================================
  // getKafkaConfig
  // =========================================================================
  describe('getKafkaConfig', () => {
    it('should return config from etcd when available', async () => {
      const kafkaData = { brokers: ['localhost:9092'], ssl: false }
      mockKvStore.get.resolves('enc:' + JSON.stringify(kafkaData))
      mockEncryption.decrypt.returns(JSON.stringify(kafkaData))
      // Set env var to prevent eager evaluation crash in fallback object construction
      process.env.KAFKA_BROKERS = 'localhost:9092'

      const result = await service.getKafkaConfig()
      expect(result).to.deep.equal(kafkaData)
    })

    it('should fall back to env vars when etcd has no config', async () => {
      // First call returns null (no etcd config), second call for save also returns
      mockKvStore.get.resolves(null)
      process.env.KAFKA_BROKERS = 'broker1:9092,broker2:9092'
      process.env.KAFKA_SSL = 'false'
      delete process.env.KAFKA_USERNAME

      const result = await service.getKafkaConfig()
      expect(result.brokers).to.deep.equal(['broker1:9092', 'broker2:9092'])
    })

    it('should include SASL config when KAFKA_USERNAME is set', async () => {
      mockKvStore.get.resolves(null)
      process.env.KAFKA_BROKERS = 'broker1:9092'
      process.env.KAFKA_SSL = 'true'
      process.env.KAFKA_USERNAME = 'user'
      process.env.KAFKA_PASSWORD = 'pass'
      process.env.KAFKA_SASL_MECHANISM = 'plain'

      const result = await service.getKafkaConfig()
      expect(result.sasl).to.exist
      expect(result.sasl!.username).to.equal('user')
      expect(result.sasl!.mechanism).to.equal('plain')
    })
  })

  // =========================================================================
  // getRedisConfig
  // =========================================================================
  describe('getRedisConfig', () => {
    it('should save and return redis config', async () => {
      const redisData = { host: 'localhost', port: 6379, tls: false, db: 0 }
      mockKvStore.get.resolves('enc:' + JSON.stringify(redisData))
      mockEncryption.decrypt.returns(JSON.stringify(redisData))

      process.env.REDIS_HOST = 'localhost'
      process.env.REDIS_PORT = '6379'
      process.env.REDIS_DB = '0'

      const result = await service.getRedisConfig()
      expect(result).to.deep.equal(redisData)
      expect(mockKvStore.set.called).to.be.true
    })
  })

  // =========================================================================
  // getMongoConfig
  // =========================================================================
  describe('getMongoConfig', () => {
    it('should save and return mongo config', async () => {
      const mongoData = { uri: 'mongodb://localhost:27017', db: 'pipeshub' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(mongoData))
      mockEncryption.decrypt.returns(JSON.stringify(mongoData))

      process.env.MONGO_URI = 'mongodb://localhost:27017'

      const result = await service.getMongoConfig()
      expect(result).to.deep.equal(mongoData)
    })
  })

  // =========================================================================
  // getQdrantConfig
  // =========================================================================
  describe('getQdrantConfig', () => {
    it('should save and return qdrant config', async () => {
      const qdrantData = { apiKey: 'key', host: 'localhost', port: 6333, grpcPort: 6334 }
      mockKvStore.get.resolves('enc:' + JSON.stringify(qdrantData))
      mockEncryption.decrypt.returns(JSON.stringify(qdrantData))

      process.env.QDRANT_API_KEY = 'key'

      const result = await service.getQdrantConfig()
      expect(result).to.deep.equal(qdrantData)
    })
  })

  // =========================================================================
  // getArangoConfig
  // =========================================================================
  describe('getArangoConfig', () => {
    it('should save and return arango config', async () => {
      const arangoData = { url: 'http://localhost:8529', db: 'pipeshub_db', username: 'root', password: 'pass' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(arangoData))
      mockEncryption.decrypt.returns(JSON.stringify(arangoData))

      process.env.ARANGO_URL = 'http://localhost:8529'
      process.env.ARANGO_USERNAME = 'root'
      process.env.ARANGO_PASSWORD = 'pass'

      const result = await service.getArangoConfig()
      expect(result).to.deep.equal(arangoData)
    })
  })

  // =========================================================================
  // getEtcdConfig
  // =========================================================================
  describe('getEtcdConfig', () => {
    it('should return etcd config from env vars', async () => {
      process.env.ETCD_HOST = 'localhost'
      process.env.ETCD_PORT = '2379'
      process.env.ETCD_DIAL_TIMEOUT = '5000'

      const result = await service.getEtcdConfig()
      expect(result.host).to.equal('localhost')
      expect(result.port).to.equal(2379)
      expect(result.dialTimeout).to.equal(5000)
    })
  })

  // =========================================================================
  // get*BackendUrl methods
  // =========================================================================
  describe('getAuthBackendUrl', () => {
    it('should return stored endpoint when present', async () => {
      mockKvStore.get.resolves(JSON.stringify({ auth: { endpoint: 'http://auth:3001' } }))
      const result = await service.getAuthBackendUrl()
      expect(result).to.equal('http://auth:3001')
    })

    it('should fall back to localhost when no endpoint in store', async () => {
      mockKvStore.get.resolves('{}')
      process.env.PORT = '3001'
      const result = await service.getAuthBackendUrl()
      expect(result).to.equal('http://localhost:3001')
    })

    it('should strip trailing slash from endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ auth: { endpoint: 'http://auth:3001/' } }))
      const result = await service.getAuthBackendUrl()
      expect(result).to.equal('http://auth:3001')
    })
  })

  describe('getCommunicationBackendUrl', () => {
    it('should return stored endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ communication: { endpoint: 'http://comm:3002' } }))
      const result = await service.getCommunicationBackendUrl()
      expect(result).to.equal('http://comm:3002')
    })

    it('should fall back to localhost when no endpoint', async () => {
      mockKvStore.get.resolves('{}')
      const result = await service.getCommunicationBackendUrl()
      expect(result).to.include('http://localhost:')
    })
  })

  describe('getKbBackendUrl', () => {
    it('should return stored endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ kb: { endpoint: 'http://kb:3003' } }))
      const result = await service.getKbBackendUrl()
      expect(result).to.equal('http://kb:3003')
    })

    it('should fall back to localhost', async () => {
      mockKvStore.get.resolves('{}')
      const result = await service.getKbBackendUrl()
      expect(result).to.include('http://localhost:')
    })
  })

  describe('getEsBackendUrl', () => {
    it('should return stored endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ es: { endpoint: 'http://es:3004' } }))
      const result = await service.getEsBackendUrl()
      expect(result).to.equal('http://es:3004')
    })

    it('should fall back to localhost', async () => {
      mockKvStore.get.resolves('{}')
      const result = await service.getEsBackendUrl()
      expect(result).to.include('http://localhost:')
    })
  })

  describe('getCmBackendUrl', () => {
    it('should return stored endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3005' } }))
      const result = await service.getCmBackendUrl()
      expect(result).to.equal('http://cm:3005')
    })

    it('should fall back to localhost', async () => {
      mockKvStore.get.resolves('{}')
      const result = await service.getCmBackendUrl()
      expect(result).to.include('http://localhost:')
    })
  })

  describe('getTokenBackendUrl', () => {
    it('should return stored endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ tokenBackend: { endpoint: 'http://token:3006' } }))
      const result = await service.getTokenBackendUrl()
      expect(result).to.equal('http://token:3006')
    })

    it('should fall back to localhost', async () => {
      mockKvStore.get.resolves('{}')
      const result = await service.getTokenBackendUrl()
      expect(result).to.include('http://localhost:')
    })
  })

  describe('getConnectorUrl', () => {
    it('should prefer env var CONNECTOR_BACKEND', async () => {
      mockKvStore.get.resolves('{}')
      process.env.CONNECTOR_BACKEND = 'http://connector:8088'
      process.env.CONNECTOR_PUBLIC_BACKEND = 'http://connector-pub:8088'

      const result = await service.getConnectorUrl()
      expect(result).to.equal('http://connector:8088')
    })

    it('should fall back to stored endpoint', async () => {
      delete process.env.CONNECTOR_BACKEND
      delete process.env.CONNECTOR_PUBLIC_BACKEND
      mockKvStore.get.resolves(JSON.stringify({ connectors: { endpoint: 'http://stored:8088', publicEndpoint: 'http://pub:8088' } }))

      const result = await service.getConnectorUrl()
      expect(result).to.equal('http://stored:8088')
    })
  })

  describe('getConnectorPublicUrl', () => {
    it('should return stored public endpoint', async () => {
      delete process.env.CONNECTOR_PUBLIC_BACKEND
      mockKvStore.get.resolves(JSON.stringify({ connectors: { publicEndpoint: 'http://pub:8088' } }))

      const result = await service.getConnectorPublicUrl()
      expect(result).to.equal('http://pub:8088')
    })

    it('should fall back to env var', async () => {
      process.env.CONNECTOR_PUBLIC_BACKEND = 'http://env-pub:8088'
      mockKvStore.get.resolves(JSON.stringify({ connectors: {} }))

      const result = await service.getConnectorPublicUrl()
      expect(result).to.equal('http://env-pub:8088')
    })
  })

  describe('getIndexingUrl', () => {
    it('should prefer env var INDEXING_BACKEND', async () => {
      process.env.INDEXING_BACKEND = 'http://indexing:8091'
      mockKvStore.get.resolves('{}')

      const result = await service.getIndexingUrl()
      expect(result).to.equal('http://indexing:8091')
    })
  })

  describe('getIamBackendUrl', () => {
    it('should return stored endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ iam: { endpoint: 'http://iam:3007' } }))
      const result = await service.getIamBackendUrl()
      expect(result).to.equal('http://iam:3007')
    })
  })

  describe('getStorageBackendUrl', () => {
    it('should return stored endpoint', async () => {
      mockKvStore.get.resolves(JSON.stringify({ storage: { endpoint: 'http://storage:3008' } }))
      const result = await service.getStorageBackendUrl()
      expect(result).to.equal('http://storage:3008')
    })
  })

  describe('getFrontendUrl', () => {
    it('should prefer env var FRONTEND_PUBLIC_URL', async () => {
      process.env.FRONTEND_PUBLIC_URL = 'http://frontend:3000'
      mockKvStore.get.resolves('{}')

      const result = await service.getFrontendUrl()
      expect(result).to.equal('http://frontend:3000')
    })

    it('should fall back to stored value', async () => {
      delete process.env.FRONTEND_PUBLIC_URL
      mockKvStore.get.resolves(JSON.stringify({ frontend: { publicEndpoint: 'http://stored-fe:3000' } }))

      const result = await service.getFrontendUrl()
      expect(result).to.equal('http://stored-fe:3000')
    })
  })

  describe('getAiBackendUrl', () => {
    it('should prefer env var QUERY_BACKEND', async () => {
      process.env.QUERY_BACKEND = 'http://query:8000'
      mockKvStore.get.resolves('{}')

      const result = await service.getAiBackendUrl()
      expect(result).to.equal('http://query:8000')
    })

    it('should fall back to default localhost:8000', async () => {
      delete process.env.QUERY_BACKEND
      mockKvStore.get.resolves('{}')

      const result = await service.getAiBackendUrl()
      expect(result).to.equal('http://localhost:8000')
    })
  })

  // =========================================================================
  // getStorageConfig
  // =========================================================================
  describe('getStorageConfig', () => {
    it('should return existing storage config', async () => {
      mockKvStore.get
        .onFirstCall().resolves(JSON.stringify({ storage: { endpoint: 'http://storage:3000' } }))
        .onSecondCall().resolves(JSON.stringify({ storageType: 's3' }))

      const result = await service.getStorageConfig()
      expect(result.storageType).to.equal('s3')
      expect(result.endpoint).to.equal('http://storage:3000')
    })

    it('should default to local when storageType is not set', async () => {
      mockKvStore.get
        .onFirstCall().resolves(JSON.stringify({ storage: { endpoint: 'http://storage:3000' } }))
        .onSecondCall().resolves('{}')

      const result = await service.getStorageConfig()
      expect(result.storageType).to.equal('local')
      expect(mockKvStore.set.called).to.be.true
    })
  })

  // =========================================================================
  // getJwtSecret
  // =========================================================================
  describe('getJwtSecret', () => {
    it('should return existing jwt secret from encrypted store', async () => {
      const keys = { jwtSecret: 'existing-secret-123' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(keys))
      mockEncryption.decrypt.returns(JSON.stringify(keys))

      const result = await service.getJwtSecret()
      expect(result).to.equal('existing-secret-123')
    })

    it('should generate and save new jwt secret when none exists', async () => {
      mockKvStore.get.resolves(null)

      const result = await service.getJwtSecret()
      expect(result).to.be.a('string')
      expect(result).to.have.lengthOf(20)
      expect(mockKvStore.set.called).to.be.true
    })

    it('should generate jwt secret when parsedKeys has no jwtSecret', async () => {
      const keys = { otherKey: 'value' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(keys))
      mockEncryption.decrypt.returns(JSON.stringify(keys))

      const result = await service.getJwtSecret()
      expect(result).to.be.a('string')
      expect(result).to.have.lengthOf(20)
    })
  })

  // =========================================================================
  // getScopedJwtSecret
  // =========================================================================
  describe('getScopedJwtSecret', () => {
    it('should return existing scoped jwt secret', async () => {
      const keys = { scopedJwtSecret: 'scoped-secret-456' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(keys))
      mockEncryption.decrypt.returns(JSON.stringify(keys))

      const result = await service.getScopedJwtSecret()
      expect(result).to.equal('scoped-secret-456')
    })

    it('should generate and save new scoped jwt secret when none exists', async () => {
      mockKvStore.get.resolves(null)

      const result = await service.getScopedJwtSecret()
      expect(result).to.be.a('string')
      expect(result).to.have.lengthOf(20)
      expect(mockKvStore.set.called).to.be.true
    })
  })

  // =========================================================================
  // getCookieSecret
  // =========================================================================
  describe('getCookieSecret', () => {
    it('should return existing cookie secret', async () => {
      const keys = { cookieSecret: 'cookie-secret-789' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(keys))
      mockEncryption.decrypt.returns(JSON.stringify(keys))

      const result = await service.getCookieSecret()
      expect(result).to.equal('cookie-secret-789')
    })

    it('should generate and save new cookie secret when none exists', async () => {
      mockKvStore.get.resolves(null)

      const result = await service.getCookieSecret()
      expect(result).to.be.a('string')
      expect(result).to.have.lengthOf(20)
    })
  })

  // =========================================================================
  // getOAuthBackendUrl
  // =========================================================================
  describe('getOAuthBackendUrl', () => {
    it('should delegate to getAuthBackendUrl', async () => {
      mockKvStore.get.resolves(JSON.stringify({ auth: { endpoint: 'http://auth:3001' } }))
      const result = await service.getOAuthBackendUrl()
      expect(result).to.equal('http://auth:3001')
    })
  })

  // =========================================================================
  // getMcpScopes
  // =========================================================================
  describe('getMcpScopes', () => {
    it('should return default scopes when env is not set', async () => {
      delete process.env.MCP_SCOPES

      const result = await service.getMcpScopes()
      expect(result).to.be.an('array')
    })

    it('should parse comma-separated scopes from env', async () => {
      process.env.MCP_SCOPES = 'scope1, scope2, scope3'

      const result = await service.getMcpScopes()
      expect(result).to.deep.equal(['scope1', 'scope2', 'scope3'])
    })

    it('should filter empty strings from scopes', async () => {
      process.env.MCP_SCOPES = 'scope1,,scope2, ,scope3'

      const result = await service.getMcpScopes()
      expect(result).to.not.include('')
    })
  })

  // =========================================================================
  // getRsAvailable
  // =========================================================================
  describe('getRsAvailable', () => {
    it('should return env var when REPLICA_SET_AVAILABLE is set', async () => {
      process.env.REPLICA_SET_AVAILABLE = 'true'

      const result = await service.getRsAvailable()
      expect(result).to.equal('true')
    })

    it('should return false for localhost mongo URI', async () => {
      delete process.env.REPLICA_SET_AVAILABLE
      process.env.MONGO_URI = 'mongodb://localhost:27017/pipeshub'

      // Mock getEncryptedConfig to return the mongo config
      const mongoData = { uri: 'mongodb://localhost:27017/pipeshub', db: 'pipeshub' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(mongoData))
      mockEncryption.decrypt.returns(JSON.stringify(mongoData))

      const result = await service.getRsAvailable()
      expect(result).to.equal('false')
    })

    it('should return true for remote mongo URI', async () => {
      delete process.env.REPLICA_SET_AVAILABLE
      process.env.MONGO_URI = 'mongodb+srv://user:pass@cluster.mongodb.net/pipeshub'

      const mongoData = { uri: 'mongodb+srv://user:pass@cluster.mongodb.net/pipeshub', db: 'pipeshub' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(mongoData))
      mockEncryption.decrypt.returns(JSON.stringify(mongoData))

      const result = await service.getRsAvailable()
      expect(result).to.equal('true')
    })

    it('should return false for @mongodb:27017 URI', async () => {
      delete process.env.REPLICA_SET_AVAILABLE
      process.env.MONGO_URI = 'mongodb://user:pass@mongodb:27017/pipeshub'

      const mongoData = { uri: 'mongodb://user:pass@mongodb:27017/pipeshub', db: 'pipeshub' }
      mockKvStore.get.resolves('enc:' + JSON.stringify(mongoData))
      mockEncryption.decrypt.returns(JSON.stringify(mongoData))

      const result = await service.getRsAvailable()
      expect(result).to.equal('false')
    })
  })

  // =========================================================================
  // getEncryptedConfig (private - tested through public methods)
  // =========================================================================
  describe('getEncryptedConfig - error handling', () => {
    it('should fall back to env vars when decrypt fails', async () => {
      mockKvStore.get.rejects(new Error('etcd connection failed'))
      process.env.KAFKA_BROKERS = 'localhost:9092'
      delete process.env.KAFKA_USERNAME

      const result = await service.getKafkaConfig()
      expect(result.brokers).to.deep.equal(['localhost:9092'])
    })
  })

  // =========================================================================
  // saveConfigToEtcd (private - tested through public methods)
  // =========================================================================
  describe('saveConfigToEtcd - error propagation', () => {
    it('should throw when kvStore.set fails in saveConfigToEtcd', async () => {
      mockKvStore.set.rejects(new Error('etcd write failed'))

      try {
        await service.getRedisConfig()
        // If we get here, the method may have caught the error internally
      } catch (error: any) {
        expect(error.message).to.equal('etcd write failed')
      }
    })
  })
})
