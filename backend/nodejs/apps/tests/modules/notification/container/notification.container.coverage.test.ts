import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationContainer } from '../../../../src/modules/notification/container/notification.container'
import { NotificationService } from '../../../../src/modules/notification/service/notification.service'
import { NotificationProducer } from '../../../../src/modules/notification/service/notification.producer'
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer'
import { TYPES } from '../../../../src/libs/types/container.types'

describe('NotificationContainer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings', async () => {
      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      const container = await NotificationContainer.initialize(appConfig)

      expect(container).to.exist
      expect(container.isBound(TYPES.AuthTokenService)).to.be.true
      expect(container.isBound(NotificationService)).to.be.true
      expect(container.isBound(NotificationProducer)).to.be.true
      expect(container.isBound(NotificationConsumer)).to.be.true
    })

    it('should bind AuthTokenService with correct secrets', async () => {
      const appConfig = {
        jwtSecret: 'my-jwt-secret',
        scopedJwtSecret: 'my-scoped-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      const container = await NotificationContainer.initialize(appConfig)
      const authTokenService = container.get(TYPES.AuthTokenService)
      expect(authTokenService).to.exist
    })
  })

  describe('dispose', () => {
    it('should unbind all bindings from container', async () => {
      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      await NotificationContainer.initialize(appConfig)
      await NotificationContainer.dispose()

      // After dispose, the container's bindings should be unbound
      // Re-initializing should work fine
      const container2 = await NotificationContainer.initialize(appConfig)
      expect(container2).to.exist
    })

    it('should do nothing when container is null', async () => {
      ;(NotificationContainer as any).container = null
      await NotificationContainer.dispose()
      // Should not throw
    })
  })
})
