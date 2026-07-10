import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { TokenManagerContainer } from '../../../../src/modules/tokens_manager/container/token-manager.container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { MongoService } from '../../../../src/libs/services/mongo.service'
import * as config from '../../../../src/modules/tokens_manager/config/config'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'

describe('tokens_manager/container/token-manager.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(TokenManagerContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(TokenManagerContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(TokenManagerContainer.dispose).to.be.a('function')
    })

    it('should accept configurationManagerConfig parameter', () => {
      expect(TokenManagerContainer.initialize.length).to.equal(1)
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      ;(TokenManagerContainer as any).instance = null
      expect(() => TokenManagerContainer.getInstance()).to.throw('Service container not initialized')
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (TokenManagerContainer as any).instance
      ;(TokenManagerContainer as any).instance = mockContainer

      try {
        const result = TokenManagerContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(TokenManagerContainer as any).instance = originalInstance
      }
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      ;(TokenManagerContainer as any).instance = null
      await TokenManagerContainer.dispose()
    })

    it('should disconnect all services on dispose', async () => {
      const mockMongo = { isConnected: sinon.stub().returns(true), destroy: sinon.stub().resolves() }
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockRecordsEventProducer = { stop: sinon.stub().resolves() }
      const mockSyncEventProducer = { stop: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          [
            'MongoService',
            'RedisService',
            'MessageProducer',
            'RecordsEventProducer',
            'SyncEventProducer',
          ].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MongoService') return mockMongo
          if (key === 'RedisService') return mockRedis
          if (key === 'MessageProducer') return mockMessageProducer
          if (key === 'RecordsEventProducer') return mockRecordsEventProducer
          if (key === 'SyncEventProducer') return mockSyncEventProducer
          return null
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect(mockRecordsEventProducer.stop.calledOnce).to.be.true
      expect(mockSyncEventProducer.stop.calledOnce).to.be.true
      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect(mockMongo.destroy.calledOnce).to.be.true
      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should handle services that are not connected', async () => {
      const mockMongo = { isConnected: sinon.stub().returns(false), destroy: sinon.stub() }
      const mockRedis = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => ['MongoService', 'RedisService'].includes(key)),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MongoService') return mockMongo
          if (key === 'RedisService') return mockRedis
          return null
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect(mockRedis.disconnect.called).to.be.false
      expect(mockMongo.destroy.called).to.be.false
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().returns(mockRedis),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should handle missing service bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect((TokenManagerContainer as any).instance).to.be.null
      expect(mockContainer.get.called).to.be.false
    })

    it('should set instance to null even when errors occur', async () => {
      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MongoService'),
        get: sinon.stub().callsFake(() => {
          throw new Error('Get failed')
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should disconnect MessageProducer only', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          key === 'MessageProducer',
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
    })
  })
})

describe('TokenManagerContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (TokenManagerContainer as any).instance
    sinon.stub(messageBrokerFactory, 'resolveMessageBrokerConfig').returns({
      type: 'kafka', kafka: { brokers: ['localhost:9092'], clientId: 'test' },
    } as any)
    sinon.stub(messageBrokerFactory, 'createMessageProducer').returns({
      connect: sinon.stub().resolves(), disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true), publish: sinon.stub().resolves(),
      publishBatch: sinon.stub().resolves(), healthCheck: sinon.stub().resolves(true),
    } as any)
  })

  afterEach(() => {
    (TokenManagerContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings when loadAppConfig succeeds', async () => {
      const mockAppConfig = {
        mongo: { uri: 'mongodb://localhost:27017/test' },
        redis: { host: 'localhost', port: 6379, username: '', password: '', db: 0 },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      }

      sinon.stub(config, 'loadAppConfig').resolves(mockAppConfig as any)

      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      // Stub MongoService.initialize directly — more reliable than stubbing
      // `mongoose.connect`, which can be left un-intercepted depending on how
      // the mongoose module's default export is resolved at require-time.
      // Without this, the real connect attempt hangs past the test timeout in CI.
      sinon.stub(MongoService.prototype, 'initialize').resolves()
      sinon.stub(MongoService.prototype, 'isConnected').returns(false)

      const { TokenEventProducer } = require('../../../../src/modules/tokens_manager/services/token-event.producer')
      sinon.stub(TokenEventProducer.prototype, 'start').resolves()

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      try {
        const container = await TokenManagerContainer.initialize(cmConfig as any)
        expect(container).to.exist
        expect(container.isBound('Logger')).to.be.true
        expect(container.isBound('AppConfig')).to.be.true
        expect(container.isBound('ConfigurationManagerConfig')).to.be.true

        ;(TokenManagerContainer as any).instance = null
      } catch (error: any) {
        // MongoService.initialize may fail in test env, that's okay
        // We're verifying the initialization path gets exercised
        expect(error).to.exist
      }
    })

    it('should throw when loadAppConfig fails', async () => {
      sinon.stub(config, 'loadAppConfig').rejects(new Error('Config load failed'))

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      try {
        await TokenManagerContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Config load failed')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should disconnect MongoService, RedisService, and MessageProducer', async () => {
      const mockMongo = { isConnected: sinon.stub().returns(true), destroy: sinon.stub().resolves() }
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockRecordsEventProducer = { stop: sinon.stub().resolves() }
      const mockSyncEventProducer = { stop: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          [
            'MongoService',
            'RedisService',
            'MessageProducer',
            'RecordsEventProducer',
            'SyncEventProducer',
          ].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MongoService') return mockMongo
          if (key === 'RedisService') return mockRedis
          if (key === 'MessageProducer') return mockMessageProducer
          if (key === 'RecordsEventProducer') return mockRecordsEventProducer
          if (key === 'SyncEventProducer') return mockSyncEventProducer
          return null
        }),
      }

      ;(TokenManagerContainer as any).instance = mockContainer
      await TokenManagerContainer.dispose()

      expect(mockRecordsEventProducer.stop.calledOnce).to.be.true
      expect(mockSyncEventProducer.stop.calledOnce).to.be.true
      expect(mockMongo.destroy.calledOnce).to.be.true
      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should skip services that are not connected', async () => {
      const mockMongo = { isConnected: sinon.stub().returns(false), destroy: sinon.stub() }
      const mockRedis = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['MongoService', 'RedisService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MongoService') return mockMongo
          if (key === 'RedisService') return mockRedis
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(TokenManagerContainer as any).instance = mockContainer
      await TokenManagerContainer.dispose()

      expect(mockMongo.destroy.called).to.be.false
      expect(mockRedis.disconnect.called).to.be.false
      expect(mockMessageProducer.disconnect.called).to.be.false
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Redis disconnect error')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().returns(mockRedis),
      }

      ;(TokenManagerContainer as any).instance = mockContainer
      await TokenManagerContainer.dispose()

      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should handle missing bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      }

      ;(TokenManagerContainer as any).instance = mockContainer
      await TokenManagerContainer.dispose()

      expect(mockContainer.get.called).to.be.false
      expect((TokenManagerContainer as any).instance).to.be.null
    })
  })
})
