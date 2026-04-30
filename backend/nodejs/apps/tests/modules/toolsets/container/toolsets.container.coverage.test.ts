import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ToolsetsContainer } from '../../../../src/modules/toolsets/container/toolsets.container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import * as config from '../../../../src/modules/tokens_manager/config/config'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'

describe('ToolsetsContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (ToolsetsContainer as any).instance
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
    (ToolsetsContainer as any).instance = originalInstance
    sinon.restore()
  })

  const cmConfig = {
    host: 'localhost',
    port: 2379,
    storeType: 'etcd' as const,
    algorithm: 'aes-256-cbc',
    secretKey: 'test-secret-key-32-chars-long!!',
  }

  describe('initialize', () => {
    it('should bind all services when config is valid', async () => {
      sinon.stub(config, 'loadAppConfig').resolves({
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
      } as any)

      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const container = await ToolsetsContainer.initialize(cmConfig as any)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('MessageProducer')).to.be.true
      expect(container.isBound('EntitiesEventProducer')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true

      ;(ToolsetsContainer as any).instance = null
    })

    it('should throw when jwtSecret is missing', async () => {
      sinon.stub(config, 'loadAppConfig').resolves({
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        scopedJwtSecret: 'test-scoped-jwt-secret',
      } as any)

      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      try {
        await ToolsetsContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('JWT secrets are missing')
      }
    })

    it('should throw when scopedJwtSecret is missing', async () => {
      sinon.stub(config, 'loadAppConfig').resolves({
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        jwtSecret: 'test-jwt-secret',
      } as any)

      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      try {
        await ToolsetsContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('JWT secrets are missing')
      }
    })

    it('should propagate errors from KeyValueStoreService.connect', async () => {
      sinon.stub(config, 'loadAppConfig').resolves({
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
      } as any)

      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV connect failed')),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(false),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      try {
        await ToolsetsContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV connect failed')
      }
    })

    it('should propagate errors when loadAppConfig fails', async () => {
      sinon.stub(config, 'loadAppConfig').rejects(new Error('Config load failed'))

      try {
        await ToolsetsContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Config load failed')
      }
    })
  })

  describe('getInstance after initialize', () => {
    it('should return the same container after initialize', async () => {
      sinon.stub(config, 'loadAppConfig').resolves({
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
      } as any)

      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const container = await ToolsetsContainer.initialize(cmConfig as any)
      expect(ToolsetsContainer.getInstance()).to.equal(container)

      ;(ToolsetsContainer as any).instance = null
    })
  })

  describe('dispose - additional coverage', () => {
    it('should skip disconnect when MessageProducer is not connected', async () => {
      const mockMessageProducer = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }
      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MessageProducer'),
        get: sinon.stub().returns(mockMessageProducer),
      }

      ;(ToolsetsContainer as any).instance = mockContainer
      await ToolsetsContainer.dispose()

      expect(mockMessageProducer.disconnect.called).to.be.false
      expect((ToolsetsContainer as any).instance).to.be.null
    })

    it('should handle missing MessageProducer binding', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      }

      ;(ToolsetsContainer as any).instance = mockContainer
      await ToolsetsContainer.dispose()

      expect(mockContainer.get.called).to.be.false
      expect((ToolsetsContainer as any).instance).to.be.null
    })
  })
})
