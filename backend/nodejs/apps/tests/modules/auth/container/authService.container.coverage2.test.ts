import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { AuthServiceContainer } from '../../../../src/modules/auth/container/authService.container'
import { Container } from 'inversify'

describe('AuthServiceContainer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getInstance', () => {
    it('should throw when not initialized', () => {
      ;(AuthServiceContainer as any).instance = null
      expect(() => AuthServiceContainer.getInstance()).to.throw(
        'Service container not initialized',
      )
    })
  })

  describe('dispose', () => {
    it('should handle dispose when no instance', async () => {
      ;(AuthServiceContainer as any).instance = null
      await AuthServiceContainer.dispose()
      // Should not throw
    })

    it('should disconnect all services during dispose', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockEventService = {
        isConnected: sinon.stub().returns(true),
        stop: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)

      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockEventService.stop.calledOnce).to.be.true
      expect((AuthServiceContainer as any).instance).to.be.null
    })

    it('should skip disconnection when services are not connected', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }
      const mockKvStore = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }
      const mockEventService = {
        isConnected: sinon.stub().returns(false),
        stop: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)

      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect(mockRedis.disconnect.called).to.be.false
      expect(mockKvStore.disconnect.called).to.be.false
      expect(mockEventService.stop.called).to.be.false
    })

    it('should handle dispose when services are not bound', async () => {
      const container = new Container()
      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect((AuthServiceContainer as any).instance).to.be.null
    })

    it('should handle errors during dispose gracefully', async () => {
      const container = new Container()
      container.bind<any>('RedisService').toConstantValue({
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Redis error')),
      })

      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect((AuthServiceContainer as any).instance).to.be.null
    })
  })
})
