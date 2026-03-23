import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { UserManagerContainer } from '../../../../src/modules/user_management/container/userManager.container'
import { Container } from 'inversify'

describe('UserManagerContainer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getInstance', () => {
    it('should throw when not initialized', () => {
      ;(UserManagerContainer as any).instance = null
      expect(() => UserManagerContainer.getInstance()).to.throw(
        'Service container not initialized',
      )
    })
  })

  describe('dispose', () => {
    it('should handle dispose when no instance', async () => {
      ;(UserManagerContainer as any).instance = null
      await UserManagerContainer.dispose()
      // Should not throw
    })

    it('should disconnect KeyValueStoreService and EntitiesEventProducer', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockEventService = {
        isConnected: sinon.stub().returns(true),
        stop: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)

      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockEventService.stop.calledOnce).to.be.true
      expect((UserManagerContainer as any).instance).to.be.null
    })

    it('should skip disconnection when services are not connected', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }
      const mockEventService = {
        isConnected: sinon.stub().returns(false),
        stop: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)

      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect(mockKvStore.disconnect.called).to.be.false
      expect(mockEventService.stop.called).to.be.false
    })

    it('should handle dispose when services are not bound', async () => {
      const container = new Container()
      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect((UserManagerContainer as any).instance).to.be.null
    })

    it('should handle errors during dispose gracefully', async () => {
      const container = new Container()
      container.bind<any>('KeyValueStoreService').toConstantValue({
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('KV error')),
      })

      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect((UserManagerContainer as any).instance).to.be.null
    })
  })
})
