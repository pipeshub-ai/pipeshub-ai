import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ToolsetsContainer } from '../../../../src/modules/toolsets/container/toolsets.container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import * as config from '../../../../src/modules/tokens_manager/config/config'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'

describe('ToolsetsContainer - coverage', () => {
  let originalInstance: unknown

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
    ;(ToolsetsContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings when loadAppConfig succeeds', async () => {
      ;(ToolsetsContainer as any).instance = null

      const mockAppConfig = {
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

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const container = await ToolsetsContainer.initialize(cmConfig as any)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('MessageProducer')).to.be.true
      expect(container.isBound('EntitiesEventProducer')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true

      expect(ToolsetsContainer.getInstance()).to.equal(container)

      ;(ToolsetsContainer as any).instance = null
    })

    it('should throw when JWT secrets are missing', async () => {
      ;(ToolsetsContainer as any).instance = null

      sinon.stub(config, 'loadAppConfig').resolves({
        jwtSecret: '',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any)

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

      try {
        await ToolsetsContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('JWT secrets are missing')
      }
    })

    it('should log and rethrow when KeyValueStoreService connect fails', async () => {
      ;(ToolsetsContainer as any).instance = null

      sinon.stub(config, 'loadAppConfig').resolves({
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any)

      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV unreachable')),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      try {
        await ToolsetsContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV unreachable')
      }
    })

    it('should throw when loadAppConfig fails', async () => {
      ;(ToolsetsContainer as any).instance = null

      sinon.stub(config, 'loadAppConfig').rejects(new Error('Config load failed'))

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      try {
        await ToolsetsContainer.initialize(cmConfig as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Config load failed')
      }
    })
  })
})
