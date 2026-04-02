import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  KVStoreMigrationService,
  MigrationConfig,
  MigrationResult,
  checkAndMigrateIfNeeded,
} from '../../../../src/libs/keyValueStore/migration/kvStoreMigration.service'

describe('libs/keyValueStore/migration/kvStoreMigration.service', () => {
  afterEach(() => {
    sinon.restore()
  })

  const mockConfig: MigrationConfig = {
    etcd: {
      host: 'localhost',
      port: 2379,
      dialTimeout: 5000,
    },
    redis: {
      host: 'localhost',
      port: 6379,
      password: 'test-password',
      db: 0,
      keyPrefix: 'pipeshub:kv:',
    },
  }

  describe('MigrationConfig interface', () => {
    it('should allow creating conforming objects with required fields', () => {
      const config: MigrationConfig = {
        etcd: { host: 'etcd-host', port: 2379 },
        redis: { host: 'redis-host', port: 6379 },
      }
      expect(config.etcd.host).to.equal('etcd-host')
      expect(config.redis.host).to.equal('redis-host')
    })

    it('should allow optional fields', () => {
      const config: MigrationConfig = {
        etcd: { host: 'etcd-host', port: 2379, dialTimeout: 10000 },
        redis: {
          host: 'redis-host',
          port: 6379,
          username: 'admin',
          password: 'secret',
          db: 1,
          tls: true,
          keyPrefix: 'custom:prefix:',
        },
      }
      expect(config.etcd.dialTimeout).to.equal(10000)
      expect(config.redis.tls).to.be.true
      expect(config.redis.keyPrefix).to.equal('custom:prefix:')
    })
  })

  describe('MigrationResult interface', () => {
    it('should allow creating conforming objects', () => {
      const result: MigrationResult = {
        success: true,
        migratedKeys: ['key1', 'key2'],
        failedKeys: [],
        skippedKeys: ['key3'],
      }
      expect(result.success).to.be.true
      expect(result.migratedKeys).to.have.lengthOf(2)
      expect(result.failedKeys).to.have.lengthOf(0)
      expect(result.skippedKeys).to.have.lengthOf(1)
    })

    it('should allow optional error field', () => {
      const result: MigrationResult = {
        success: false,
        migratedKeys: [],
        failedKeys: ['key1'],
        skippedKeys: [],
        error: 'Connection failed',
      }
      expect(result.error).to.equal('Connection failed')
    })
  })

  describe('KVStoreMigrationService', () => {
    it('should be a class that can be instantiated', () => {
      expect(KVStoreMigrationService).to.be.a('function')
      const service = new KVStoreMigrationService(mockConfig)
      expect(service).to.be.instanceOf(KVStoreMigrationService)
    })

    it('should have isMigrationCompleted method', () => {
      const service = new KVStoreMigrationService(mockConfig)
      expect(service.isMigrationCompleted).to.be.a('function')
    })

    it('should have isEtcdAvailable method', () => {
      const service = new KVStoreMigrationService(mockConfig)
      expect(service.isEtcdAvailable).to.be.a('function')
    })

    it('should have hasRedisData method', () => {
      const service = new KVStoreMigrationService(mockConfig)
      expect(service.hasRedisData).to.be.a('function')
    })

    it('should have migrate method', () => {
      const service = new KVStoreMigrationService(mockConfig)
      expect(service.migrate).to.be.a('function')
    })

    it('should store the config during construction', () => {
      const service = new KVStoreMigrationService(mockConfig)
      expect((service as any).config).to.deep.equal(mockConfig)
    })

    it('should initialize with null etcd and redis clients', () => {
      const service = new KVStoreMigrationService(mockConfig)
      expect((service as any).etcdClient).to.be.null
      expect((service as any).redisClient).to.be.null
    })

    describe('isMigrationCompleted', () => {
      it('should return false when Redis connection fails', async () => {
        const service = new KVStoreMigrationService({
          etcd: { host: 'non-existent', port: 2379 },
          redis: { host: 'non-existent', port: 1 },
        })

        const result = await service.isMigrationCompleted()
        expect(result).to.be.false
      })
    })

    describe('isEtcdAvailable', () => {
      it('should return false when etcd is not available', async () => {
        const service = new KVStoreMigrationService({
          etcd: { host: 'non-existent', port: 1, dialTimeout: 100 },
          redis: { host: 'localhost', port: 6379 },
        })

        const result = await service.isEtcdAvailable()
        expect(result).to.be.false
      })
    })

    describe('hasRedisData', () => {
      it('should return false when Redis connection fails', async () => {
        const service = new KVStoreMigrationService({
          etcd: { host: 'localhost', port: 2379 },
          redis: { host: 'non-existent', port: 1 },
        })

        const result = await service.hasRedisData()
        expect(result).to.be.false
      })
    })

    describe('migrate', () => {
      it('should return failure when etcd is not available', async () => {
        const service = new KVStoreMigrationService({
          etcd: { host: 'non-existent', port: 1, dialTimeout: 100 },
          redis: { host: 'localhost', port: 6379 },
        })

        const result = await service.migrate()
        expect(result.success).to.be.false
        expect(result.error).to.include('not available')
      })
    })

    describe('private methods', () => {
      it('disconnect should handle null clients', async () => {
        const service = new KVStoreMigrationService(mockConfig)
        // Both clients are null by default
        await (service as any).disconnect()
        // Should not throw
        expect((service as any).etcdClient).to.be.null
        expect((service as any).redisClient).to.be.null
      })
    })

    describe('keyPrefix handling', () => {
      it('should use default keyPrefix when not configured', () => {
        const configNoPrefix: MigrationConfig = {
          etcd: { host: 'localhost', port: 2379 },
          redis: { host: 'localhost', port: 6379 },
        }
        const service = new KVStoreMigrationService(configNoPrefix)
        expect((service as any).config.redis.keyPrefix).to.be.undefined
      })

      it('should use configured keyPrefix', () => {
        const configWithPrefix: MigrationConfig = {
          etcd: { host: 'localhost', port: 2379 },
          redis: { host: 'localhost', port: 6379, keyPrefix: 'custom:' },
        }
        const service = new KVStoreMigrationService(configWithPrefix)
        expect((service as any).config.redis.keyPrefix).to.equal('custom:')
      })
    })
  })

  describe('KVStoreMigrationService - private method coverage', () => {
    it('setMigrationCompleted should set flag key in Redis', async () => {
      const service = new KVStoreMigrationService(mockConfig)
      const mockRedisClient = { set: sinon.stub().resolves('OK') }
      ;(service as any).redisClient = mockRedisClient

      await (service as any).setMigrationCompleted()

      expect(mockRedisClient.set.calledOnce).to.be.true
      const [key, value] = mockRedisClient.set.firstCall.args
      expect(key).to.include('/migrations/etcd_to_redis')
      expect(value).to.equal('true')
    })

    it('publishCacheInvalidation should publish message to Redis channel', async () => {
      const service = new KVStoreMigrationService(mockConfig)
      const mockRedisClient = { publish: sinon.stub().resolves(1) }
      ;(service as any).redisClient = mockRedisClient

      await (service as any).publishCacheInvalidation()

      expect(mockRedisClient.publish.calledOnce).to.be.true
      const [channel, message] = mockRedisClient.publish.firstCall.args
      expect(channel).to.equal('pipeshub:cache:invalidate')
      expect(message).to.equal('__CLEAR_ALL__')
    })

    it('publishCacheInvalidation should handle errors gracefully', async () => {
      const service = new KVStoreMigrationService(mockConfig)
      const mockRedisClient = { publish: sinon.stub().rejects(new Error('Redis error')) }
      ;(service as any).redisClient = mockRedisClient

      // Should not throw
      await (service as any).publishCacheInvalidation()
    })

    it('connect should initialize both etcd and redis clients', async () => {
      const service = new KVStoreMigrationService(mockConfig)

      try {
        await (service as any).connect()
      } catch {
        // Connection may fail but clients should be set
      }

      // etcdClient and redisClient should be set (even if connections fail later)
      expect((service as any).etcdClient).to.not.be.null
      expect((service as any).redisClient).to.not.be.null

      // Cleanup
      try {
        await (service as any).disconnect()
      } catch {
        // Ignore cleanup errors
      }
    })

    it('disconnect should close and null out both clients', async () => {
      const service = new KVStoreMigrationService(mockConfig)
      const mockEtcd = { close: sinon.stub().resolves() }
      const mockRedis = { quit: sinon.stub().resolves() }
      ;(service as any).etcdClient = mockEtcd
      ;(service as any).redisClient = mockRedis

      await (service as any).disconnect()

      expect(mockEtcd.close.calledOnce).to.be.true
      expect(mockRedis.quit.calledOnce).to.be.true
      expect((service as any).etcdClient).to.be.null
      expect((service as any).redisClient).to.be.null
    })

    it('migrate should handle overall exception and return failure', async () => {
      const service = new KVStoreMigrationService(mockConfig)

      sinon.stub(service, 'isEtcdAvailable').resolves(true)
      sinon.stub(service as any, 'connect').rejects(new Error('Connection refused'))

      const result = await service.migrate()

      expect(result.success).to.be.false
      expect(result.error).to.include('Connection refused')
    })

    it('migrate should handle null values by skipping keys', async () => {
      const service = new KVStoreMigrationService(mockConfig)

      sinon.stub(service, 'isEtcdAvailable').resolves(true)
      sinon.stub(service as any, 'connect').resolves()
      sinon.stub(service as any, 'disconnect').resolves()

      const mockGetAll = { keys: sinon.stub().resolves(['key1']) }
      const mockGet = { string: sinon.stub().resolves(null) }
      ;(service as any).etcdClient = {
        getAll: sinon.stub().returns(mockGetAll),
        get: sinon.stub().returns(mockGet),
        close: sinon.stub().resolves(),
      }
      ;(service as any).redisClient = {
        set: sinon.stub().resolves(),
        quit: sinon.stub().resolves(),
      }

      const result = await service.migrate()

      expect(result.skippedKeys).to.include('key1')
      expect(result.migratedKeys).to.be.empty
    })

    it('migrate should handle per-key errors', async () => {
      const service = new KVStoreMigrationService(mockConfig)

      sinon.stub(service, 'isEtcdAvailable').resolves(true)
      sinon.stub(service as any, 'connect').resolves()
      sinon.stub(service as any, 'disconnect').resolves()

      const mockGetAll = { keys: sinon.stub().resolves(['key1']) }
      const mockGet = { string: sinon.stub().rejects(new Error('etcd read error')) }
      ;(service as any).etcdClient = {
        getAll: sinon.stub().returns(mockGetAll),
        get: sinon.stub().returns(mockGet),
        close: sinon.stub().resolves(),
      }
      ;(service as any).redisClient = {
        set: sinon.stub().resolves(),
        quit: sinon.stub().resolves(),
      }

      const result = await service.migrate()

      expect(result.failedKeys).to.include('key1')
      expect(result.success).to.be.false
    })

    it('migrate should successfully migrate keys and set flag', async () => {
      const service = new KVStoreMigrationService(mockConfig)

      sinon.stub(service, 'isEtcdAvailable').resolves(true)
      sinon.stub(service as any, 'connect').resolves()
      sinon.stub(service as any, 'disconnect').resolves()
      sinon.stub(service as any, 'setMigrationCompleted').resolves()
      sinon.stub(service as any, 'publishCacheInvalidation').resolves()

      const mockGetAll = { keys: sinon.stub().resolves(['key1', 'key2']) }
      ;(service as any).etcdClient = {
        getAll: sinon.stub().returns(mockGetAll),
        get: sinon.stub().returns({ string: sinon.stub().resolves('value') }),
        close: sinon.stub().resolves(),
      }
      ;(service as any).redisClient = {
        set: sinon.stub().resolves(),
        quit: sinon.stub().resolves(),
      }

      const result = await service.migrate()

      expect(result.success).to.be.true
      expect(result.migratedKeys).to.have.lengthOf(2)
      expect(result.failedKeys).to.be.empty
    })

    it('migrate should use default keyPrefix when not configured', async () => {
      const configNoPrefix: MigrationConfig = {
        etcd: { host: 'localhost', port: 2379 },
        redis: { host: 'localhost', port: 6379 },
      }
      const service = new KVStoreMigrationService(configNoPrefix)

      sinon.stub(service, 'isEtcdAvailable').resolves(true)
      sinon.stub(service as any, 'connect').resolves()
      sinon.stub(service as any, 'disconnect').resolves()
      sinon.stub(service as any, 'setMigrationCompleted').resolves()
      sinon.stub(service as any, 'publishCacheInvalidation').resolves()

      const mockSetStub = sinon.stub().resolves()
      const mockGetAll = { keys: sinon.stub().resolves(['key1']) }
      ;(service as any).etcdClient = {
        getAll: sinon.stub().returns(mockGetAll),
        get: sinon.stub().returns({ string: sinon.stub().resolves('val') }),
        close: sinon.stub().resolves(),
      }
      ;(service as any).redisClient = {
        set: mockSetStub,
        quit: sinon.stub().resolves(),
      }

      await service.migrate()

      expect(mockSetStub.firstCall.args[0]).to.include('pipeshub:kv:key1')
    })
  })

  describe('checkAndMigrateIfNeeded', () => {
    it('should be exported as a function', () => {
      expect(checkAndMigrateIfNeeded).to.be.a('function')
    })

    it('should return null when migration already completed', async () => {
      // Stub the prototype methods to simulate a completed migration
      const isMigrationCompletedStub = sinon.stub(
        KVStoreMigrationService.prototype,
        'isMigrationCompleted',
      ).resolves(true)

      const result = await checkAndMigrateIfNeeded(mockConfig)
      expect(result).to.be.null
      expect(isMigrationCompletedStub.calledOnce).to.be.true
    })

    it('should return null when etcd is not available and migration not completed', async () => {
      sinon.stub(
        KVStoreMigrationService.prototype,
        'isMigrationCompleted',
      ).resolves(false)

      sinon.stub(
        KVStoreMigrationService.prototype,
        'isEtcdAvailable',
      ).resolves(false)

      const result = await checkAndMigrateIfNeeded(mockConfig)
      expect(result).to.be.null
    })

    it('should call migrate when etcd is available and migration not completed', async () => {
      sinon.stub(
        KVStoreMigrationService.prototype,
        'isMigrationCompleted',
      ).resolves(false)

      sinon.stub(
        KVStoreMigrationService.prototype,
        'isEtcdAvailable',
      ).resolves(true)

      const mockMigrationResult: MigrationResult = {
        success: true,
        migratedKeys: ['key1'],
        failedKeys: [],
        skippedKeys: [],
      }

      sinon.stub(
        KVStoreMigrationService.prototype,
        'migrate',
      ).resolves(mockMigrationResult)

      const result = await checkAndMigrateIfNeeded(mockConfig)
      expect(result).to.deep.equal(mockMigrationResult)
    })
  })
})
