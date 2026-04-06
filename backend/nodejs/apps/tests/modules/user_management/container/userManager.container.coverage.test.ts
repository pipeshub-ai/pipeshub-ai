import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { UserManagerContainer } from '../../../../src/modules/user_management/container/userManager.container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'

describe('UserManagerContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (UserManagerContainer as any).instance

    // Stub message broker factory to prevent real Kafka/Redis connections
    sinon.stub(messageBrokerFactory, 'resolveMessageBrokerConfig').returns({
      type: 'kafka',
      kafka: { brokers: ['localhost:9092'], clientId: 'test' },
    } as any)
    sinon.stub(messageBrokerFactory, 'createMessageProducer').returns({
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true),
      publish: sinon.stub().resolves(),
      publishBatch: sinon.stub().resolves(),
      healthCheck: sinon.stub().resolves(true),
    } as any)
  })

  afterEach(() => {
    (UserManagerContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings', async function () {
      this.timeout(30000)
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
        frontendUrl: 'http://localhost:3000',
      } as any

      const container = await UserManagerContainer.initialize(cmConfig as any, appConfig)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('MailService')).to.be.true
      expect(container.isBound('AuthService')).to.be.true
      expect(container.isBound('ConfigurationManagerService')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('EntitiesEventProducer')).to.be.true
      expect(container.isBound('OrgController')).to.be.true
      expect(container.isBound('TeamsController')).to.be.true
      expect(container.isBound('UserController')).to.be.true
      expect(container.isBound('UserGroupController')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true

      const instance = UserManagerContainer.getInstance()
      expect(instance).to.equal(container)

      ;(UserManagerContainer as any).instance = null
    })

    it('should throw when KeyValueStoreService connect fails', async () => {
      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV store down')),
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
        redis: { host: 'localhost', port: 6379 },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        iamBackend: 'http://localhost:3001',
        cmBackend: 'http://localhost:3004',
        communicationBackend: 'http://localhost:3002',
      } as any

      try {
        await UserManagerContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV store down')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should disconnect KeyValueStoreService and MessageProducer', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(UserManagerContainer as any).instance = mockContainer
      await UserManagerContainer.dispose()

      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect((UserManagerContainer as any).instance).to.be.null
    })

    it('should skip disconnect when services are not connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(UserManagerContainer as any).instance = mockContainer
      await UserManagerContainer.dispose()

      expect(mockKvStore.disconnect.called).to.be.false
      expect(mockMessageProducer.disconnect.called).to.be.false
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      ;(UserManagerContainer as any).instance = mockContainer
      await UserManagerContainer.dispose()

      expect((UserManagerContainer as any).instance).to.be.null
    })
  })
})
