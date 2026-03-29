import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { AuthServiceContainer } from '../../../../src/modules/auth/container/authService.container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'

describe('AuthServiceContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (AuthServiceContainer as any).instance
  })

  afterEach(() => {
    (AuthServiceContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all service bindings', async () => {
      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
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
        iamBackend: 'http://localhost:3001',
        cmBackend: 'http://localhost:3004',
        communicationBackend: 'http://localhost:3002',
      } as any

      const container = await AuthServiceContainer.initialize(cmConfig as any, appConfig)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('RedisService')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true
      expect(container.isBound('IamService')).to.be.true
      expect(container.isBound('MailService')).to.be.true
      expect(container.isBound('SessionService')).to.be.true
      expect(container.isBound('ConfigurationManagerService')).to.be.true
      expect(container.isBound('EntitiesEventProducer')).to.be.true
      expect(container.isBound('JitProvisioningService')).to.be.true
      expect(container.isBound('SamlController')).to.be.true
      expect(container.isBound('UserAccountController')).to.be.true

      // Verify getInstance works after initialization
      const instance = AuthServiceContainer.getInstance()
      expect(instance).to.equal(container)

      // Clean up
      ;(AuthServiceContainer as any).instance = null
    })

    it('should throw when KeyValueStoreService connect fails', async () => {
      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV connection failed')),
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
        iamBackend: 'http://localhost:3001',
        cmBackend: 'http://localhost:3004',
        communicationBackend: 'http://localhost:3002',
      } as any

      try {
        await AuthServiceContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV connection failed')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should disconnect RedisService when connected and skip others when not bound', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'RedisService') return mockRedis
          return null
        }),
      }

      ;(AuthServiceContainer as any).instance = mockContainer
      await AuthServiceContainer.dispose()

      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect((AuthServiceContainer as any).instance).to.be.null
    })

    it('should disconnect KeyValueStoreService when connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      ;(AuthServiceContainer as any).instance = mockContainer
      await AuthServiceContainer.dispose()

      expect(mockKvStore.disconnect.calledOnce).to.be.true
    })

    it('should stop EntitiesEventProducer when connected', async () => {
      const mockEntityEvents = { isConnected: sinon.stub().returns(true), stop: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'EntitiesEventProducer'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'EntitiesEventProducer') return mockEntityEvents
          return null
        }),
      }

      ;(AuthServiceContainer as any).instance = mockContainer
      await AuthServiceContainer.dispose()

      expect(mockEntityEvents.stop.calledOnce).to.be.true
    })
  })
})
