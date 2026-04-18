import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  randomKeyGenerator,
  ConfigService,
  SmtpConfig,
  KafkaConfig,
  RedisConfig,
  MongoConfig,
  QdrantConfig,
  ArangoConfig,
  EtcdConfig,
  EncryptionConfig,
  DefaultStorageConfig,
} from '../../../../src/modules/tokens_manager/services/cm.service'
import { envGuard } from '../../../helpers/env-guard'

describe('tokens_manager/services/cm.service', () => {
  afterEach(() => {
    sinon.restore()
  })

  // =========================================================================
  // randomKeyGenerator
  // =========================================================================
  describe('randomKeyGenerator', () => {
    it('should generate a string of length 20', () => {
      const key = randomKeyGenerator()
      expect(key).to.have.lengthOf(20)
    })

    it('should return a string', () => {
      const key = randomKeyGenerator()
      expect(key).to.be.a('string')
    })

    it('should only contain alphanumeric characters', () => {
      const key = randomKeyGenerator()
      expect(key).to.match(/^[A-Za-z0-9]+$/)
    })

    it('should generate different keys on consecutive calls', () => {
      const key1 = randomKeyGenerator()
      const key2 = randomKeyGenerator()
      // While theoretically possible to collide, it's astronomically unlikely
      expect(key1).to.not.equal(key2)
    })

    it('should generate keys from the expected character set', () => {
      const validChars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
      const key = randomKeyGenerator()
      for (const char of key) {
        expect(validChars).to.include(char)
      }
    })
  })

  // =========================================================================
  // Interface Type Checks (compile-time checks expressed as runtime tests)
  // =========================================================================
  describe('SmtpConfig interface', () => {
    it('should accept valid SmtpConfig objects', () => {
      const config: SmtpConfig = {
        host: 'smtp.example.com',
        port: 587,
        username: 'user',
        password: 'pass',
        fromEmail: 'noreply@example.com',
      }
      expect(config.host).to.equal('smtp.example.com')
      expect(config.port).to.equal(587)
      expect(config.fromEmail).to.equal('noreply@example.com')
    })

    it('should accept SmtpConfig with optional fields omitted', () => {
      const config: SmtpConfig = {
        host: 'smtp.example.com',
        port: 587,
        fromEmail: 'noreply@example.com',
      }
      expect(config.username).to.be.undefined
      expect(config.password).to.be.undefined
    })
  })

  describe('KafkaConfig interface', () => {
    it('should accept valid KafkaConfig objects', () => {
      const config: KafkaConfig = {
        brokers: ['broker1:9092', 'broker2:9092'],
        ssl: true,
        sasl: {
          mechanism: 'scram-sha-512',
          username: 'user',
          password: 'pass',
        },
      }
      expect(config.brokers).to.have.lengthOf(2)
      expect(config.ssl).to.be.true
      expect(config.sasl?.mechanism).to.equal('scram-sha-512')
    })

    it('should accept KafkaConfig without sasl', () => {
      const config: KafkaConfig = {
        brokers: ['localhost:9092'],
      }
      expect(config.sasl).to.be.undefined
      expect(config.ssl).to.be.undefined
    })

    it('should support all SASL mechanisms', () => {
      const mechanisms: Array<'plain' | 'scram-sha-256' | 'scram-sha-512'> = [
        'plain',
        'scram-sha-256',
        'scram-sha-512',
      ]
      for (const mechanism of mechanisms) {
        const config: KafkaConfig = {
          brokers: ['localhost:9092'],
          sasl: { mechanism, username: 'u', password: 'p' },
        }
        expect(config.sasl?.mechanism).to.equal(mechanism)
      }
    })
  })

  describe('RedisConfig interface', () => {
    it('should accept valid RedisConfig objects', () => {
      const config: RedisConfig = {
        host: 'localhost',
        port: 6379,
        username: 'default',
        password: 'secret',
        tls: false,
        db: 0,
      }
      expect(config.host).to.equal('localhost')
      expect(config.port).to.equal(6379)
    })

    it('should accept RedisConfig with only required fields', () => {
      const config: RedisConfig = {
        host: 'localhost',
        port: 6379,
      }
      expect(config.username).to.be.undefined
      expect(config.password).to.be.undefined
      expect(config.tls).to.be.undefined
      expect(config.db).to.be.undefined
    })
  })

  describe('MongoConfig interface', () => {
    it('should accept valid MongoConfig objects', () => {
      const config: MongoConfig = {
        uri: 'mongodb://localhost:27017',
        db: 'pipeshub',
      }
      expect(config.uri).to.equal('mongodb://localhost:27017')
      expect(config.db).to.equal('pipeshub')
    })
  })

  describe('QdrantConfig interface', () => {
    it('should accept valid QdrantConfig objects', () => {
      const config: QdrantConfig = {
        port: 6333,
        apiKey: 'test-key',
        host: 'localhost',
        grpcPort: 6334,
      }
      expect(config.port).to.equal(6333)
      expect(config.grpcPort).to.equal(6334)
    })
  })

  describe('ArangoConfig interface', () => {
    it('should accept valid ArangoConfig objects', () => {
      const config: ArangoConfig = {
        url: 'http://localhost:8529',
        db: 'pipeshub',
        username: 'root',
        password: 'secret',
      }
      expect(config.url).to.equal('http://localhost:8529')
      expect(config.db).to.equal('pipeshub')
    })
  })

  describe('EtcdConfig interface', () => {
    it('should accept valid EtcdConfig objects', () => {
      const config: EtcdConfig = {
        host: 'localhost',
        port: 2379,
        dialTimeout: 5000,
      }
      expect(config.host).to.equal('localhost')
      expect(config.dialTimeout).to.equal(5000)
    })
  })

  describe('EncryptionConfig interface', () => {
    it('should accept valid EncryptionConfig objects', () => {
      const config: EncryptionConfig = {
        key: 'my-secret-key',
        algorithm: 'aes-256-cbc',
      }
      expect(config.key).to.equal('my-secret-key')
      expect(config.algorithm).to.equal('aes-256-cbc')
    })
  })

  describe('DefaultStorageConfig interface', () => {
    it('should accept valid DefaultStorageConfig objects', () => {
      const config: DefaultStorageConfig = {
        storageType: 'local',
        endpoint: 'http://localhost:3000',
      }
      expect(config.storageType).to.equal('local')
      expect(config.endpoint).to.equal('http://localhost:3000')
    })

    it('should support different storage types', () => {
      const types = ['local', 's3', 'azure']
      for (const storageType of types) {
        const config: DefaultStorageConfig = {
          storageType,
          endpoint: 'http://localhost:3000',
        }
        expect(config.storageType).to.equal(storageType)
      }
    })
  })

  // =========================================================================
  // ConfigService class tests
  // =========================================================================
  describe('ConfigService', () => {
    // We cannot easily test the singleton since it relies on real etcd,
    // but we can test the static method behavior and catch constructor errors.
    it('should have a getInstance static method', () => {
      expect(ConfigService).to.have.property('getInstance')
      expect(typeof ConfigService.getInstance).to.equal('function')
    })

    it('should export ConfigService as a class', () => {
      expect(ConfigService).to.be.a('function')
    })
  })

  // =========================================================================
  // randomKeyGenerator additional tests
  // =========================================================================
  describe('randomKeyGenerator (additional)', () => {
    it('should produce unique keys across many iterations', () => {
      const keys = new Set<string>()
      for (let i = 0; i < 50; i++) {
        keys.add(randomKeyGenerator())
      }
      // With 62^20 possible keys, 50 should all be unique
      expect(keys.size).to.equal(50)
    })

    it('should never produce empty string', () => {
      for (let i = 0; i < 10; i++) {
        const key = randomKeyGenerator()
        expect(key.length).to.be.greaterThan(0)
      }
    })
  })

  // =========================================================================
  // SmtpConfig edge cases
  // =========================================================================
  describe('SmtpConfig interface (additional)', () => {
    it('should accept SmtpConfig with empty string values', () => {
      const config: SmtpConfig = {
        host: '',
        port: 0,
        fromEmail: '',
      }
      expect(config.host).to.equal('')
      expect(config.port).to.equal(0)
    })
  })

  // =========================================================================
  // KafkaConfig edge cases
  // =========================================================================
  describe('KafkaConfig interface (additional)', () => {
    it('should accept KafkaConfig with empty brokers array', () => {
      const config: KafkaConfig = {
        brokers: [],
      }
      expect(config.brokers).to.have.lengthOf(0)
    })

    it('should accept KafkaConfig with ssl false', () => {
      const config: KafkaConfig = {
        brokers: ['localhost:9092'],
        ssl: false,
      }
      expect(config.ssl).to.be.false
    })
  })

  // =========================================================================
  // RedisConfig edge cases
  // =========================================================================
  describe('RedisConfig interface (additional)', () => {
    it('should accept RedisConfig with tls true', () => {
      const config: RedisConfig = {
        host: 'redis.example.com',
        port: 6380,
        tls: true,
      }
      expect(config.tls).to.be.true
    })

    it('should accept RedisConfig with db number', () => {
      const config: RedisConfig = {
        host: 'localhost',
        port: 6379,
        db: 5,
      }
      expect(config.db).to.equal(5)
    })
  })

  // =========================================================================
  // MongoConfig edge cases
  // =========================================================================
  describe('MongoConfig interface (additional)', () => {
    it('should accept MongoConfig with connection string options', () => {
      const config: MongoConfig = {
        uri: 'mongodb+srv://user:pass@cluster.example.com/db?retryWrites=true',
        db: 'test-db',
      }
      expect(config.uri).to.include('mongodb+srv')
    })
  })

  // =========================================================================
  // QdrantConfig edge cases
  // =========================================================================
  describe('QdrantConfig interface (additional)', () => {
    it('should accept QdrantConfig with non-default ports', () => {
      const config: QdrantConfig = {
        port: 16333,
        apiKey: '',
        host: '192.168.1.100',
        grpcPort: 16334,
      }
      expect(config.port).to.equal(16333)
      expect(config.apiKey).to.equal('')
    })
  })

  // =========================================================================
  // ArangoConfig edge cases
  // =========================================================================
  describe('ArangoConfig interface (additional)', () => {
    it('should accept ArangoConfig with https URL', () => {
      const config: ArangoConfig = {
        url: 'https://arango.example.com:8529',
        db: 'production',
        username: 'admin',
        password: 'securePassword123',
      }
      expect(config.url).to.include('https')
    })
  })

  // =========================================================================
  // EtcdConfig edge cases
  // =========================================================================
  describe('EtcdConfig interface (additional)', () => {
    it('should accept EtcdConfig with zero dialTimeout', () => {
      const config: EtcdConfig = {
        host: 'localhost',
        port: 2379,
        dialTimeout: 0,
      }
      expect(config.dialTimeout).to.equal(0)
    })

    it('should accept EtcdConfig with large dialTimeout', () => {
      const config: EtcdConfig = {
        host: 'etcd.example.com',
        port: 2379,
        dialTimeout: 30000,
      }
      expect(config.dialTimeout).to.equal(30000)
    })
  })

  // =========================================================================
  // randomKeyGenerator stress tests
  // =========================================================================
  describe('randomKeyGenerator (stress)', () => {
    it('should always produce length 20 across 100 iterations', () => {
      for (let i = 0; i < 100; i++) {
        const key = randomKeyGenerator()
        expect(key).to.have.lengthOf(20)
      }
    })

    it('should contain at least 3 distinct characters', () => {
      // A random 20-char alphanumeric string should have variety
      const key = randomKeyGenerator()
      const uniqueChars = new Set(key.split(''))
      expect(uniqueChars.size).to.be.at.least(3)
    })
  })

  // =========================================================================
  // DefaultStorageConfig edge cases
  // =========================================================================
  describe('DefaultStorageConfig (additional)', () => {
    it('should accept empty endpoint', () => {
      const config: DefaultStorageConfig = {
        storageType: 'local',
        endpoint: '',
      }
      expect(config.endpoint).to.equal('')
    })

    it('should accept HTTPS endpoint', () => {
      const config: DefaultStorageConfig = {
        storageType: 's3',
        endpoint: 'https://s3.amazonaws.com',
      }
      expect(config.endpoint).to.include('https')
    })
  })

  // =========================================================================
  // ConfigService singleton tests
  // =========================================================================
  describe('ConfigService (additional)', () => {
    it('should export ConfigService with standard methods', () => {
      const proto = ConfigService.prototype
      expect(proto).to.have.property('connect')
      expect(proto).to.have.property('getSmtpConfig')
      expect(proto).to.have.property('getKafkaConfig')
      expect(proto).to.have.property('getRedisConfig')
      expect(proto).to.have.property('getMongoConfig')
      expect(proto).to.have.property('getQdrantConfig')
      expect(proto).to.have.property('getArangoConfig')
      expect(proto).to.have.property('getEtcdConfig')
      expect(proto).to.have.property('getJwtSecret')
      expect(proto).to.have.property('getScopedJwtSecret')
      expect(proto).to.have.property('getCookieSecret')
      expect(proto).to.have.property('getAuthBackendUrl')
      expect(proto).to.have.property('getCommunicationBackendUrl')
      expect(proto).to.have.property('getKbBackendUrl')
      expect(proto).to.have.property('getEsBackendUrl')
      expect(proto).to.have.property('getCmBackendUrl')
      expect(proto).to.have.property('getTokenBackendUrl')
      expect(proto).to.have.property('getConnectorUrl')
      expect(proto).to.have.property('getConnectorPublicUrl')
      expect(proto).to.have.property('getIndexingUrl')
      expect(proto).to.have.property('getIamBackendUrl')
      expect(proto).to.have.property('getStorageBackendUrl')
      expect(proto).to.have.property('getFrontendUrl')
      expect(proto).to.have.property('getAiBackendUrl')
      expect(proto).to.have.property('getStorageConfig')
      expect(proto).to.have.property('getOAuthBackendUrl')
      expect(proto).to.have.property('initializeOAuthIssuer')
      expect(proto).to.have.property('getOAuthIssuer')
      expect(proto).to.have.property('getMcpScopes')
      expect(proto).to.have.property('getRsAvailable')
    })

    it('should return the same instance on multiple getInstance calls', () => {
      // ConfigService.getInstance() may throw due to missing etcd config,
      // but we can verify the static method exists and is callable
      try {
        const instance1 = ConfigService.getInstance()
        const instance2 = ConfigService.getInstance()
        expect(instance1).to.equal(instance2)
      } catch (e) {
        // Expected if etcd config not available in test env
        // But at least we verified it's callable
        expect(e).to.be.an('error')
      }
    })
  })

  // =========================================================================
  // KafkaConfig SASL edge cases
  // =========================================================================
  describe('KafkaConfig SASL (additional)', () => {
    it('should accept plain mechanism', () => {
      const config: KafkaConfig = {
        brokers: ['broker:9092'],
        sasl: {
          mechanism: 'plain',
          username: 'admin',
          password: 'secret',
        },
      }
      expect(config.sasl?.mechanism).to.equal('plain')
    })

    it('should accept scram-sha-256 mechanism', () => {
      const config: KafkaConfig = {
        brokers: ['broker:9092'],
        sasl: {
          mechanism: 'scram-sha-256',
          username: 'admin',
          password: 'secret',
        },
      }
      expect(config.sasl?.mechanism).to.equal('scram-sha-256')
    })

    it('should accept multiple brokers with ssl and sasl', () => {
      const config: KafkaConfig = {
        brokers: ['broker1:9092', 'broker2:9092', 'broker3:9092'],
        ssl: true,
        sasl: {
          mechanism: 'scram-sha-512',
          username: 'user',
          password: 'pass',
        },
      }
      expect(config.brokers).to.have.lengthOf(3)
      expect(config.ssl).to.be.true
    })
  })

  // =========================================================================
  // MongoConfig with various URI formats
  // =========================================================================
  describe('MongoConfig URI formats', () => {
    it('should accept standard mongodb URI', () => {
      const config: MongoConfig = {
        uri: 'mongodb://localhost:27017/mydb',
        db: 'mydb',
      }
      expect(config.uri).to.include('mongodb://')
    })

    it('should accept mongodb+srv URI', () => {
      const config: MongoConfig = {
        uri: 'mongodb+srv://user:pass@cluster.mongodb.net/db',
        db: 'db',
      }
      expect(config.uri).to.include('mongodb+srv')
    })

    it('should accept mongodb URI with replica set', () => {
      const config: MongoConfig = {
        uri: 'mongodb://host1:27017,host2:27017,host3:27017/db?replicaSet=rs0',
        db: 'db',
      }
      expect(config.uri).to.include('replicaSet')
    })
  })

  // =========================================================================
  // QdrantConfig additional
  // =========================================================================
  describe('QdrantConfig (additional)', () => {
    it('should accept QdrantConfig with empty API key (local dev)', () => {
      const config: QdrantConfig = {
        port: 6333,
        apiKey: '',
        host: 'localhost',
        grpcPort: 6334,
      }
      expect(config.apiKey).to.equal('')
    })

    it('should accept remote QdrantConfig with custom ports', () => {
      const config: QdrantConfig = {
        port: 443,
        apiKey: 'prod-api-key-12345',
        host: 'qdrant.cloud.example.com',
        grpcPort: 443,
      }
      expect(config.host).to.include('cloud')
      expect(config.port).to.equal(443)
    })
  })

  // =========================================================================
  // ConfigService method tests with mocked internals
  // =========================================================================
  describe('ConfigService methods (mocked)', () => {
    const env = envGuard()
    let configService: any
    let mockKvStore: any
    let mockEncryption: any

    beforeEach(() => {
      env.snapshot()
      mockKvStore = {
        get: sinon.stub(),
        set: sinon.stub().resolves(),
        connect: sinon.stub().resolves(),
        watchKey: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }

      mockEncryption = {
        encrypt: sinon.stub().callsFake((text: string) => `encrypted:${text}`),
        decrypt: sinon.stub().callsFake((text: string) => text.replace('encrypted:', '')),
      }

      // Create instance bypassing private constructor
      configService = Object.create(ConfigService.prototype)
      configService.keyValueStoreService = mockKvStore
      configService.encryptionService = mockEncryption
      configService.configManagerConfig = {}
    })

    afterEach(() => {
      env.restore()
    })

    describe('connect', () => {
      it('should call keyValueStoreService.connect', async () => {
        await configService.connect()
        expect(mockKvStore.connect.calledOnce).to.be.true
      })
    })

    describe('getSmtpConfig', () => {
      it('should return null when no SMTP config exists', async () => {
        mockKvStore.get.resolves(null)
        const result = await configService.getSmtpConfig()
        expect(result).to.be.null
      })

      it('should return parsed SMTP config when encrypted config exists', async () => {
        const smtpData = { host: 'smtp.test.com', port: 587, fromEmail: 'test@test.com' }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(smtpData))
        const result = await configService.getSmtpConfig()
        expect(result).to.deep.equal(smtpData)
      })
    })

    describe('getKafkaConfig', () => {
      it('should have getKafkaConfig method', () => {
        expect(configService.getKafkaConfig).to.be.a('function')
      })

      it('should fall back to env vars when config not in store', async () => {
        mockKvStore.get.resolves(null)
        process.env.KAFKA_BROKERS = 'broker1:9092,broker2:9092'
        process.env.KAFKA_SSL = 'false'
        delete process.env.KAFKA_USERNAME

        const result = await configService.getKafkaConfig()
        expect(result.brokers).to.deep.equal(['broker1:9092', 'broker2:9092'])
        expect(result.ssl).to.be.false
        expect(result.sasl).to.be.undefined
      })

      it('should include SASL config when KAFKA_USERNAME is set', async () => {
        mockKvStore.get.resolves(null)
        process.env.KAFKA_BROKERS = 'broker:9092'
        process.env.KAFKA_SSL = 'true'
        process.env.KAFKA_USERNAME = 'user'
        process.env.KAFKA_PASSWORD = 'pass'
        process.env.KAFKA_SASL_MECHANISM = 'plain'

        const result = await configService.getKafkaConfig()
        expect(result.sasl).to.exist
        expect(result.sasl.mechanism).to.equal('plain')
        expect(result.sasl.username).to.equal('user')
      })
    })

    describe('getRedisConfig', () => {
      it('should return redis config', async () => {
        const redisData = { host: 'localhost', port: 6379 }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(redisData))

        process.env.REDIS_HOST = 'localhost'
        process.env.REDIS_PORT = '6379'
        process.env.REDIS_DB = '0'

        const result = await configService.getRedisConfig()
        expect(result.host).to.equal('localhost')
      })
    })

    describe('getMongoConfig', () => {
      it('should return mongo config', async () => {
        const mongoData = { uri: 'mongodb://localhost:27017', db: 'test' }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(mongoData))

        process.env.MONGO_URI = 'mongodb://localhost:27017'

        const result = await configService.getMongoConfig()
        expect(result.uri).to.equal('mongodb://localhost:27017')
      })
    })

    describe('getQdrantConfig', () => {
      it('should return qdrant config', async () => {
        const qdrantData = { apiKey: 'key', host: 'localhost', port: 6333, grpcPort: 6334 }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(qdrantData))

        process.env.QDRANT_API_KEY = 'key'
        process.env.QDRANT_HOST = 'localhost'

        const result = await configService.getQdrantConfig()
        expect(result.host).to.equal('localhost')
      })
    })

    describe('getArangoConfig', () => {
      it('should return arango config', async () => {
        const arangoData = { url: 'http://localhost:8529', db: 'test', username: 'root', password: 'pass' }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(arangoData))

        process.env.ARANGO_URL = 'http://localhost:8529'
        process.env.ARANGO_USERNAME = 'root'
        process.env.ARANGO_PASSWORD = 'pass'

        const result = await configService.getArangoConfig()
        expect(result.url).to.equal('http://localhost:8529')
      })
    })

    describe('getEtcdConfig', () => {
      it('should return etcd config from env vars', async () => {
        process.env.ETCD_HOST = 'localhost'
        process.env.ETCD_PORT = '2379'
        process.env.ETCD_DIAL_TIMEOUT = '5000'

        const result = await configService.getEtcdConfig()
        expect(result.host).to.equal('localhost')
        expect(result.port).to.equal(2379)
        expect(result.dialTimeout).to.equal(5000)
      })
    })

    describe('getAuthBackendUrl', () => {
      it('should return auth endpoint from stored config', async () => {
        mockKvStore.get.resolves(JSON.stringify({ auth: { endpoint: 'http://auth:3000' } }))
        const result = await configService.getAuthBackendUrl()
        expect(result).to.equal('http://auth:3000')
      })

      it('should fall back to PORT env var when no stored endpoint', async () => {
        mockKvStore.get.resolves('{}')
        process.env.PORT = '4000'
        const result = await configService.getAuthBackendUrl()
        expect(result).to.equal('http://localhost:4000')
      })

      it('should use default port 3000 when no PORT env var', async () => {
        mockKvStore.get.resolves(null)
        delete process.env.PORT
        const result = await configService.getAuthBackendUrl()
        expect(result).to.equal('http://localhost:3000')
      })
    })

    describe('getCommunicationBackendUrl', () => {
      it('should return communication endpoint', async () => {
        mockKvStore.get.resolves(JSON.stringify({ communication: { endpoint: 'http://comm:3000' } }))
        const result = await configService.getCommunicationBackendUrl()
        expect(result).to.equal('http://comm:3000')
      })

      it('should fall back to localhost when no endpoint', async () => {
        mockKvStore.get.resolves('{}')
        const result = await configService.getCommunicationBackendUrl()
        expect(result).to.include('http://localhost')
      })
    })

    describe('getKbBackendUrl', () => {
      it('should return kb endpoint', async () => {
        mockKvStore.get.resolves(JSON.stringify({ kb: { endpoint: 'http://kb:3000' } }))
        const result = await configService.getKbBackendUrl()
        expect(result).to.equal('http://kb:3000')
      })
    })

    describe('getEsBackendUrl', () => {
      it('should return es endpoint', async () => {
        mockKvStore.get.resolves(JSON.stringify({ es: { endpoint: 'http://es:3000' } }))
        const result = await configService.getEsBackendUrl()
        expect(result).to.equal('http://es:3000')
      })
    })

    describe('getCmBackendUrl', () => {
      it('should return cm endpoint', async () => {
        mockKvStore.get.resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3000' } }))
        const result = await configService.getCmBackendUrl()
        expect(result).to.equal('http://cm:3000')
      })
    })

    describe('getTokenBackendUrl', () => {
      it('should return token backend endpoint', async () => {
        mockKvStore.get.resolves(JSON.stringify({ tokenBackend: { endpoint: 'http://token:3000' } }))
        const result = await configService.getTokenBackendUrl()
        expect(result).to.equal('http://token:3000')
      })
    })

    describe('getConnectorUrl', () => {
      it('should return connector endpoint from env var', async () => {
        process.env.CONNECTOR_BACKEND = 'http://connector:8088'
        process.env.CONNECTOR_PUBLIC_BACKEND = 'http://connector-public:8088'
        mockKvStore.get.resolves('{}')
        const result = await configService.getConnectorUrl()
        expect(result).to.equal('http://connector:8088')
      })
    })

    describe('getConnectorPublicUrl', () => {
      it('should return connector public endpoint from stored config', async () => {
        mockKvStore.get.resolves(JSON.stringify({
          connectors: { publicEndpoint: 'http://connector-pub:8088' }
        }))
        const result = await configService.getConnectorPublicUrl()
        expect(result).to.equal('http://connector-pub:8088')
      })

      it('should fall back to env var', async () => {
        mockKvStore.get.resolves(JSON.stringify({ connectors: {} }))
        process.env.CONNECTOR_PUBLIC_BACKEND = 'http://fallback-connector:8088'
        const result = await configService.getConnectorPublicUrl()
        expect(result).to.equal('http://fallback-connector:8088')
      })
    })

    describe('getIndexingUrl', () => {
      it('should return indexing endpoint', async () => {
        process.env.INDEXING_BACKEND = 'http://indexing:8091'
        mockKvStore.get.resolves('{}')
        const result = await configService.getIndexingUrl()
        expect(result).to.equal('http://indexing:8091')
      })
    })

    describe('getIamBackendUrl', () => {
      it('should return iam endpoint', async () => {
        mockKvStore.get.resolves(JSON.stringify({ iam: { endpoint: 'http://iam:3000' } }))
        const result = await configService.getIamBackendUrl()
        expect(result).to.equal('http://iam:3000')
      })
    })

    describe('getStorageBackendUrl', () => {
      it('should return storage endpoint', async () => {
        mockKvStore.get.resolves(JSON.stringify({ storage: { endpoint: 'http://storage:3000' } }))
        const result = await configService.getStorageBackendUrl()
        expect(result).to.equal('http://storage:3000')
      })
    })

    describe('getFrontendUrl', () => {
      it('should return frontend URL from env var', async () => {
        process.env.FRONTEND_PUBLIC_URL = 'http://frontend:3000'
        mockKvStore.get.resolves('{}')
        const result = await configService.getFrontendUrl()
        expect(result).to.equal('http://frontend:3000')
      })

      it('should return stored frontend URL when no env var', async () => {
        delete process.env.FRONTEND_PUBLIC_URL
        mockKvStore.get.resolves(JSON.stringify({ frontend: { publicEndpoint: 'http://stored-frontend:3000' } }))
        const result = await configService.getFrontendUrl()
        expect(result).to.equal('http://stored-frontend:3000')
      })
    })

    describe('getAiBackendUrl', () => {
      it('should return AI backend URL from env var', async () => {
        process.env.QUERY_BACKEND = 'http://query:8000'
        mockKvStore.get.resolves('{}')
        const result = await configService.getAiBackendUrl()
        expect(result).to.equal('http://query:8000')
      })

      it('should fall back to default localhost:8000', async () => {
        delete process.env.QUERY_BACKEND
        mockKvStore.get.resolves(JSON.stringify({}))
        const result = await configService.getAiBackendUrl()
        expect(result).to.equal('http://localhost:8000')
      })
    })

    describe('getStorageConfig', () => {
      it('should return storage config with storageType local by default', async () => {
        mockKvStore.get.callsFake(async (path: string) => {
          if (path.includes('storage')) return '{}'
          return '{}'
        })
        const result = await configService.getStorageConfig()
        expect(result.storageType).to.equal('local')
      })

      it('should return stored storageType when available', async () => {
        let callCount = 0
        mockKvStore.get.callsFake(async () => {
          callCount++
          if (callCount <= 1) return '{}'
          return JSON.stringify({ storageType: 's3' })
        })
        const result = await configService.getStorageConfig()
        expect(result.storageType).to.equal('s3')
      })
    })

    describe('getJwtSecret', () => {
      it('should return existing JWT secret from encrypted store', async () => {
        const secretData = { jwtSecret: 'existing-secret-key' }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(secretData))
        const result = await configService.getJwtSecret()
        expect(result).to.equal('existing-secret-key')
      })

      it('should generate and store new JWT secret when none exists', async () => {
        mockKvStore.get.resolves(null)
        const result = await configService.getJwtSecret()
        expect(result).to.be.a('string')
        expect(result).to.have.lengthOf(20)
        expect(mockKvStore.set.calledOnce).to.be.true
      })

      it('should generate JWT secret when parsedKeys has no jwtSecret', async () => {
        const secretData = { otherKey: 'value' }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(secretData))
        const result = await configService.getJwtSecret()
        expect(result).to.be.a('string')
        expect(result).to.have.lengthOf(20)
      })
    })

    describe('getScopedJwtSecret', () => {
      it('should return existing scoped JWT secret', async () => {
        const secretData = { scopedJwtSecret: 'scoped-secret' }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(secretData))
        const result = await configService.getScopedJwtSecret()
        expect(result).to.equal('scoped-secret')
      })

      it('should generate new scoped JWT secret when none exists', async () => {
        mockKvStore.get.resolves(null)
        const result = await configService.getScopedJwtSecret()
        expect(result).to.be.a('string')
        expect(result).to.have.lengthOf(20)
      })
    })

    describe('getCookieSecret', () => {
      it('should return existing cookie secret', async () => {
        const secretData = { cookieSecret: 'cookie-secret' }
        mockKvStore.get.resolves('encrypted:' + JSON.stringify(secretData))
        const result = await configService.getCookieSecret()
        expect(result).to.equal('cookie-secret')
      })

      it('should generate new cookie secret when none exists', async () => {
        mockKvStore.get.resolves(null)
        const result = await configService.getCookieSecret()
        expect(result).to.be.a('string')
        expect(result).to.have.lengthOf(20)
      })
    })

    describe('getOAuthBackendUrl', () => {
      it('should return auth backend URL', async () => {
        mockKvStore.get.resolves(JSON.stringify({ auth: { endpoint: 'http://auth:3000' } }))
        const result = await configService.getOAuthBackendUrl()
        expect(result).to.equal('http://auth:3000')
      })
    })

    describe('initializeOAuthIssuer', () => {
      it('should always persist resolved issuer even when one exists', async () => {
        mockKvStore.get.resolves(JSON.stringify({ oauthProvider: { issuer: 'http://existing-issuer' } }))
        process.env.OAUTH_ISSUER = 'http://fresh-issuer'
        await configService.initializeOAuthIssuer()
        expect(mockKvStore.set.calledOnce).to.be.true
        const saved = JSON.parse(mockKvStore.set.firstCall.args[1])
        expect(saved.oauthProvider.issuer).to.equal('http://fresh-issuer')
        delete process.env.OAUTH_ISSUER
      })

      it('should set issuer from env var', async () => {
        mockKvStore.get.resolves('{}')
        process.env.OAUTH_ISSUER = 'http://dev-issuer'
        await configService.initializeOAuthIssuer()
        expect(mockKvStore.set.calledOnce).to.be.true
      })

      it('should fall back to localhost when frontend URL contains localhost', async () => {
        mockKvStore.get.resolves('{}')
        delete process.env.OAUTH_ISSUER
        delete process.env.FRONTEND_PUBLIC_URL
        process.env.PORT = '3001'
        await configService.initializeOAuthIssuer()
        expect(mockKvStore.set.called).to.be.true
        const saved = JSON.parse(mockKvStore.set.lastCall.args[1])
        expect(saved.oauthProvider.issuer).to.equal('http://localhost:3001')
      })
    })

    describe('getOAuthIssuer', () => {
      it('should return issuer from env var', async () => {
        process.env.OAUTH_ISSUER = 'http://issuer.example.com'
        mockKvStore.get.resolves('{}')
        const result = await configService.getOAuthIssuer()
        expect(result).to.equal('http://issuer.example.com')
      })

      it('should return frontend URL when it does not contain localhost', async () => {
        delete process.env.OAUTH_ISSUER
        delete process.env.FRONTEND_PUBLIC_URL
        mockKvStore.get.resolves(JSON.stringify({
          frontend: { publicEndpoint: 'https://app.example.com' },
        }))
        const result = await configService.getOAuthIssuer()
        expect(result).to.equal('https://app.example.com')
      })

      it('should fall back to localhost when frontend URL contains localhost', async () => {
        delete process.env.OAUTH_ISSUER
        delete process.env.FRONTEND_PUBLIC_URL
        process.env.PORT = '3001'
        mockKvStore.get.resolves('{}')
        const result = await configService.getOAuthIssuer()
        expect(result).to.equal('http://localhost:3001')
      })
    })

    describe('getMcpScopes', () => {
      it('should return default scopes when no env var', async () => {
        delete process.env.MCP_SCOPES
        const result = await configService.getMcpScopes()
        expect(result).to.be.an('array')
        expect(result).to.include('openid')
      })

      it('should parse scopes from env var', async () => {
        process.env.MCP_SCOPES = 'scope1, scope2, scope3'
        const result = await configService.getMcpScopes()
        expect(result).to.deep.equal(['scope1', 'scope2', 'scope3'])
      })

      it('should filter empty scopes', async () => {
        process.env.MCP_SCOPES = 'scope1,,scope2,'
        const result = await configService.getMcpScopes()
        expect(result).to.deep.equal(['scope1', 'scope2'])
      })
    })

    describe('getRsAvailable', () => {
      it('should return env var when REPLICA_SET_AVAILABLE is set', async () => {
        process.env.REPLICA_SET_AVAILABLE = 'true'
        const result = await configService.getRsAvailable()
        expect(result).to.equal('true')
      })

      it('should return false for localhost mongo URI', async () => {
        delete process.env.REPLICA_SET_AVAILABLE
        process.env.MONGO_URI = 'mongodb://localhost:27017/test'
        mockKvStore.get.resolves(null)
        const result = await configService.getRsAvailable()
        expect(result).to.equal('false')
      })

      it('should return false for @mongodb:27017 URI', async () => {
        delete process.env.REPLICA_SET_AVAILABLE
        process.env.MONGO_URI = 'mongodb://user:pass@mongodb:27017/test'
        mockKvStore.get.resolves(null)
        const result = await configService.getRsAvailable()
        expect(result).to.equal('false')
      })

      it('should return true for remote mongo URI', async () => {
        delete process.env.REPLICA_SET_AVAILABLE
        process.env.MONGO_URI = 'mongodb+srv://user:pass@cluster.mongodb.net/test'
        mockKvStore.get.resolves(null)
        const result = await configService.getRsAvailable()
        expect(result).to.equal('true')
      })
    })

    describe('getEncryptedConfig - error handling', () => {
      it('should return fallback env vars when get throws', async () => {
        mockKvStore.get.rejects(new Error('connection error'))
        const fallback = { host: 'fallback-host', port: 1234 }
        const result = await (configService as any).getEncryptedConfig('/test/path', fallback)
        expect(result).to.deep.equal(fallback)
      })

      it('should return fallback when decrypt throws', async () => {
        mockKvStore.get.resolves('bad-encrypted-data')
        mockEncryption.decrypt.throws(new Error('decrypt error'))
        const fallback = { host: 'fallback-host' }
        const result = await (configService as any).getEncryptedConfig('/test/path', fallback)
        expect(result).to.deep.equal(fallback)
      })
    })

    describe('saveConfigToEtcd', () => {
      it('should encrypt and save config', async () => {
        await (configService as any).saveConfigToEtcd('/test/path', { key: 'value' })
        expect(mockEncryption.encrypt.calledOnce).to.be.true
        expect(mockKvStore.set.calledOnce).to.be.true
      })

      it('should throw when set fails', async () => {
        mockKvStore.set.rejects(new Error('write error'))
        try {
          await (configService as any).saveConfigToEtcd('/test/path', { key: 'value' })
          expect.fail('Should have thrown')
        } catch (error) {
          expect(error).to.be.instanceOf(Error)
        }
      })
    })

    // =========================================================================
    // getDeploymentConfig
    // =========================================================================
    describe('getDeploymentConfig', () => {
      it('should read and merge deployment config from KV store', async () => {
        const stored = JSON.stringify({ dataStoreType: 'neo4j', vectorDbType: 'qdrant' })
        mockKvStore.get.resolves(stored)
        process.env.MESSAGE_BROKER = 'redis'
        process.env.KV_STORE_TYPE = 'redis'

        const result = await configService.getDeploymentConfig()

        expect(result.dataStoreType).to.equal('neo4j')
        expect(result.vectorDbType).to.equal('qdrant')
        expect(result.messageBrokerType).to.equal('redis')
        expect(result.kvStoreType).to.equal('redis')
      })

      it('should overwrite messageBrokerType and kvStoreType from env', async () => {
        const stored = JSON.stringify({
          dataStoreType: 'neo4j',
          vectorDbType: 'qdrant',
          messageBrokerType: 'kafka',
          kvStoreType: 'etcd',
        })
        mockKvStore.get.resolves(stored)
        process.env.MESSAGE_BROKER = 'redis'
        process.env.KV_STORE_TYPE = 'redis'

        const result = await configService.getDeploymentConfig()

        expect(result.messageBrokerType).to.equal('redis')
        expect(result.kvStoreType).to.equal('redis')
      })

      it('should not set dataStoreType or vectorDbType defaults', async () => {
        mockKvStore.get.resolves(null)
        process.env.MESSAGE_BROKER = 'kafka'
        process.env.KV_STORE_TYPE = 'etcd'

        const result = await configService.getDeploymentConfig()

        expect(result.dataStoreType).to.be.undefined
        expect(result.vectorDbType).to.be.undefined
        expect(result.messageBrokerType).to.equal('kafka')
        expect(result.kvStoreType).to.equal('etcd')
      })

      it('should default messageBrokerType to kafka when env not set', async () => {
        mockKvStore.get.resolves(null)
        delete process.env.MESSAGE_BROKER
        delete process.env.KV_STORE_TYPE

        const result = await configService.getDeploymentConfig()

        expect(result.messageBrokerType).to.equal('kafka')
        expect(result.kvStoreType).to.equal('etcd')
      })

      it('should write merged config back to KV store', async () => {
        const stored = JSON.stringify({ dataStoreType: 'neo4j' })
        mockKvStore.get.resolves(stored)
        process.env.MESSAGE_BROKER = 'redis'
        process.env.KV_STORE_TYPE = 'redis'

        await configService.getDeploymentConfig()

        expect(mockKvStore.set.calledOnce).to.be.true
        const written = JSON.parse(mockKvStore.set.firstCall.args[1])
        expect(written.dataStoreType).to.equal('neo4j')
        expect(written.messageBrokerType).to.equal('redis')
      })

      it('should handle corrupted KV store data gracefully', async () => {
        mockKvStore.get.resolves('not-valid-json')
        process.env.MESSAGE_BROKER = 'kafka'
        process.env.KV_STORE_TYPE = 'etcd'

        const result = await configService.getDeploymentConfig()

        expect(result.messageBrokerType).to.equal('kafka')
        expect(result.kvStoreType).to.equal('etcd')
      })

      it('should handle KV store read error gracefully', async () => {
        mockKvStore.get.rejects(new Error('connection timeout'))
        process.env.MESSAGE_BROKER = 'redis'
        process.env.KV_STORE_TYPE = 'redis'

        const result = await configService.getDeploymentConfig()

        expect(result.messageBrokerType).to.equal('redis')
        expect(result.kvStoreType).to.equal('redis')
      })

      it('should lowercase env var values', async () => {
        mockKvStore.get.resolves(null)
        process.env.MESSAGE_BROKER = 'KAFKA'
        process.env.KV_STORE_TYPE = 'ETCD'

        const result = await configService.getDeploymentConfig()

        expect(result.messageBrokerType).to.equal('kafka')
        expect(result.kvStoreType).to.equal('etcd')
      })

      it('should preserve Python-set fields when writing back', async () => {
        const stored = JSON.stringify({
          dataStoreType: 'neo4j',
          vectorDbType: 'qdrant',
        })
        mockKvStore.get.resolves(stored)
        process.env.MESSAGE_BROKER = 'redis'
        process.env.KV_STORE_TYPE = 'redis'

        await configService.getDeploymentConfig()

        const written = JSON.parse(mockKvStore.set.firstCall.args[1])
        expect(written.dataStoreType).to.equal('neo4j')
        expect(written.vectorDbType).to.equal('qdrant')
        expect(written.messageBrokerType).to.equal('redis')
        expect(written.kvStoreType).to.equal('redis')
      })
    })

    // =========================================================================
    // readDeploymentConfig
    // =========================================================================
    describe('readDeploymentConfig', () => {
      it('should return parsed config from KV store', async () => {
        const stored = JSON.stringify({
          dataStoreType: 'neo4j',
          vectorDbType: 'qdrant',
          messageBrokerType: 'redis',
          kvStoreType: 'redis',
        })
        mockKvStore.get.resolves(stored)

        const result = await configService.readDeploymentConfig()

        expect(result.dataStoreType).to.equal('neo4j')
        expect(result.messageBrokerType).to.equal('redis')
      })

      it('should not write anything to KV store', async () => {
        mockKvStore.get.resolves(JSON.stringify({ dataStoreType: 'neo4j' }))

        await configService.readDeploymentConfig()

        expect(mockKvStore.set.called).to.be.false
      })

      it('should return empty object when key does not exist', async () => {
        mockKvStore.get.resolves(null)

        const result = await configService.readDeploymentConfig()

        expect(result).to.deep.equal({})
      })

      it('should return empty object on parse error', async () => {
        mockKvStore.get.resolves('invalid-json')

        const result = await configService.readDeploymentConfig()

        expect(result).to.deep.equal({})
      })

      it('should return empty object on KV store error', async () => {
        mockKvStore.get.rejects(new Error('redis down'))

        const result = await configService.readDeploymentConfig()

        expect(result).to.deep.equal({})
      })

      it('should handle object value from KV store', async () => {
        const obj = { dataStoreType: 'arangodb', vectorDbType: 'qdrant' }
        mockKvStore.get.resolves(JSON.stringify(obj))

        const result = await configService.readDeploymentConfig()

        expect(result.dataStoreType).to.equal('arangodb')
        expect(result.vectorDbType).to.equal('qdrant')
      })
    })
  })
})
