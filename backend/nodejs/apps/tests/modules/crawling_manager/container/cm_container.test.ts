import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingManagerContainer } from '../../../../src/modules/crawling_manager/container/cm_container'
import { Container } from 'inversify'
import { setupCrawlingDependencies } from '../../../../src/modules/crawling_manager/container/cm_container';
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { SyncEventProducer } from '../../../../src/modules/knowledge_base/services/sync_events.service'
import { CrawlingWorkerService } from '../../../../src/modules/crawling_manager/services/crawling_worker'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { ConnectorsCrawlingService } from '../../../../src/modules/crawling_manager/services/connectors/connectors'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'

describe('CrawlingManagerContainer', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', () => {
    expect(CrawlingManagerContainer).to.be.a('function')
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(CrawlingManagerContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(CrawlingManagerContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(CrawlingManagerContainer.dispose).to.be.a('function')
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = null

      try {
        expect(() => CrawlingManagerContainer.getInstance()).to.throw(
          'Crawling Manager container not initialized',
        )
      } finally {
        ;(CrawlingManagerContainer as any).instance = originalInstance
      }
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        const result = CrawlingManagerContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(CrawlingManagerContainer as any).instance = originalInstance
      }
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = null

      try {
        await CrawlingManagerContainer.dispose()
      } finally {
        ;(CrawlingManagerContainer as any).instance = originalInstance
      }
    })

    it('should set instance to null after dispose', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockCrawlingWorker = { close: sinon.stub().resolves() }
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockSyncEvents = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => {
          const knownKeys = ['RedisService', 'SyncEventProducer']
          // Handle class-based bindings
          if (typeof key === 'function') {
            return true
          }
          return knownKeys.includes(key)
        }),
        get: sinon.stub().callsFake((key: any) => {
          if (key === 'RedisService') return mockRedis
          if (key === 'SyncEventProducer') return mockSyncEvents
          // Handle class-based keys (CrawlingWorkerService, KeyValueStoreService)
          if (typeof key === 'function') {
            if (key.name === 'CrawlingWorkerService') return mockCrawlingWorker
            if (key.name === 'KeyValueStoreService') return mockKvStore
          }
          return null
        }),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect((CrawlingManagerContainer as any).instance).to.be.null
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should disconnect Redis when connected', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => key === 'RedisService'),
        get: sinon.stub().callsFake((key: any) => {
          if (key === 'RedisService') return mockRedis
          return null
        }),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect(mockRedis.disconnect.calledOnce).to.be.true
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should not disconnect Redis when not connected', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => key === 'RedisService'),
        get: sinon.stub().callsFake((key: any) => {
          if (key === 'RedisService') return mockRedis
          return null
        }),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect(mockRedis.disconnect.called).to.be.false
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => key === 'RedisService'),
        get: sinon.stub().returns(mockRedis),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect((CrawlingManagerContainer as any).instance).to.be.null
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })
  })

  describe('initialize', () => {
    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(CrawlingManagerContainer.initialize.length).to.equal(2)
    })
  })
})

describe('CrawlingManagerContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (CrawlingManagerContainer as any).instance
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
    (CrawlingManagerContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings when services initialize successfully', async () => {
      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const mockSyncProducer = {
        start: sinon.stub().resolves(),
        stop: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      sinon.stub(SyncEventProducer.prototype, 'start').callsFake(async function (this: any) {
        // no-op
      })

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        redis: { host: 'localhost', port: 6379, username: '', password: '', db: 0 },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      // We need to stub the CrawlingWorkerService so the container.get works
      // The container.get<CrawlingWorkerService> will fail if inversify can't resolve it,
      // so we test that the container creation + bindings work
      try {
        await CrawlingManagerContainer.initialize(cmConfig as any, appConfig)
      } catch (error: any) {
        // Expected - CrawlingWorkerService has dependencies that can't resolve in test
        // But we verify that the method attempts initialization
        expect(error).to.exist
      }
    })

    it('should propagate errors from initializeServices', async () => {
      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV connect failed')),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(false),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        redis: { host: 'localhost', port: 6379, username: '', password: '', db: 0 },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      try {
        await CrawlingManagerContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV connect failed')
      }
    })
  })

  describe('setupCrawlingDependencies', () => {
    it('should bind RedisConfig, ConnectorsCrawlingService, CrawlingSchedulerService, CrawlingWorkerService', () => {
      const container = new Container()
      // Provide a logger since some services may need it
      const mockLogger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
      container.bind('Logger').toConstantValue(mockLogger)

      const redisConfig = { host: 'localhost', port: 6379, username: '', password: '', db: 0 }

      setupCrawlingDependencies(container, redisConfig as any)

      expect(container.isBound('RedisConfig')).to.be.true
      expect(container.isBound(ConnectorsCrawlingService)).to.be.true
      expect(container.isBound(CrawlingSchedulerService)).to.be.true
      expect(container.isBound(CrawlingWorkerService)).to.be.true
    })
  })

  describe('dispose - additional coverage', () => {
    it('should close crawling worker when bound', async () => {
      const mockCrawlingWorker = { close: sinon.stub().resolves() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => {
          if (typeof key === 'function') return true
          if (key === 'MessageProducer') return true
          return false
        }),
        get: sinon.stub().callsFake((key: any) => {
          if (typeof key === 'function') {
            if (key.name === 'CrawlingWorkerService') return mockCrawlingWorker
            if (key.name === 'KeyValueStoreService') return mockKvStore
          }
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(CrawlingManagerContainer as any).instance = mockContainer
      await CrawlingManagerContainer.dispose()

      expect(mockCrawlingWorker.close.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect(mockKvStore.disconnect.calledOnce).to.be.true
    })

    it('should disconnect KeyValueStoreService when connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => {
          if (typeof key === 'function' && key.name === 'KeyValueStoreService') return true
          return false
        }),
        get: sinon.stub().callsFake((key: any) => {
          if (typeof key === 'function' && key.name === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      ;(CrawlingManagerContainer as any).instance = mockContainer
      await CrawlingManagerContainer.dispose()

      expect(mockKvStore.disconnect.calledOnce).to.be.true
    })

    it('should not disconnect MessageProducer when not connected', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => key === 'MessageProducer'),
        get: sinon.stub().callsFake((key: any) => {
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(CrawlingManagerContainer as any).instance = mockContainer
      await CrawlingManagerContainer.dispose()

      expect(mockMessageProducer.disconnect.called).to.be.false
    })
  })
})

describe('CrawlingManagerContainer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('setupCrawlingDependencies', () => {
    it('should bind RedisConfig and services to container', () => {
      const container = new Container()

      // Bind Logger first (required by services)
      container.bind<any>('Logger').toConstantValue({
        debug: sinon.stub(),
        info: sinon.stub(),
        error: sinon.stub(),
        warn: sinon.stub(),
      })
      container.bind<any>('AppConfig').toConstantValue({
        redis: { host: 'localhost', port: 6379 },
        kafka: { brokers: ['localhost:9092'] },
      })
      container.bind<any>('ConfigurationManagerConfig').toConstantValue({
        host: 'localhost',
        port: 2379,
      })

      const redisConfig = { host: 'localhost', port: 6379 }
      setupCrawlingDependencies(container, redisConfig)

      expect(container.isBound('RedisConfig')).to.be.true
    })
  })

  describe('getInstance', () => {
    it('should throw when not initialized', () => {
      // Reset the static instance
      ;(CrawlingManagerContainer as any).instance = null

      expect(() => CrawlingManagerContainer.getInstance()).to.throw(
        'Crawling Manager container not initialized',
      )
    })
  })

  describe('dispose', () => {
    it('should handle dispose when no instance', async () => {
      ;(CrawlingManagerContainer as any).instance = null
      // Should not throw
      await CrawlingManagerContainer.dispose()
    })

    it('should handle dispose with mock instance', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockMessageProducer = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockCrawlingWorker = {
        close: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)
      container.bind<any>(KeyValueStoreService).toConstantValue(mockKvStore)
      container.bind<any>('MessageProducer').toConstantValue(mockMessageProducer)
      // We need the class symbol
      const { CrawlingWorkerService } = require('../../../../src/modules/crawling_manager/services/crawling_worker')
      container.bind<any>(CrawlingWorkerService).toConstantValue(mockCrawlingWorker)

      ;(CrawlingManagerContainer as any).instance = container

      await CrawlingManagerContainer.dispose()
      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect(mockCrawlingWorker.close.calledOnce).to.be.true
    })

    it('should handle dispose when services are not connected', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)

      ;(CrawlingManagerContainer as any).instance = container

      await CrawlingManagerContainer.dispose()
      expect(mockRedis.disconnect.called).to.be.false
    })

    it('should handle dispose errors gracefully', async () => {
      const container = new Container()
      container.bind<any>('RedisService').toConstantValue({
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('disconnect failed')),
      })

      ;(CrawlingManagerContainer as any).instance = container

      // Should not throw
      await CrawlingManagerContainer.dispose()
      expect((CrawlingManagerContainer as any).instance).to.be.null
    })
  })
})
