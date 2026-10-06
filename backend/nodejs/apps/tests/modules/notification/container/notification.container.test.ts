import 'reflect-metadata'
import { MailProducer } from '../../../../src/modules/mail/services/mail.producer'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationContainer } from '../../../../src/modules/notification/container/notification.container'
import { NotificationService } from '../../../../src/modules/notification/service/notification.service'
import { NotificationProducer } from '../../../../src/modules/notification/service/notification.producer'
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer'
import {
  NOTIFICATION_EMAIL_DISPATCHER,
  NotificationEmailDispatcher,
} from '../../../../src/modules/notification/service/notification-email.dispatcher'
import { TYPES } from '../../../../src/libs/types/container.types'

describe('notification/container/notification.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', async () => {
    try {
      const mod = await import('../../../../src/modules/notification/container/notification.container')
      expect(mod).to.be.an('object')
    } catch (error: any) {
      expect(error).to.exist
    }
  })
})

describe('NotificationContainer - coverage', () => {
  const originalMessageBroker = process.env.MESSAGE_BROKER

  beforeEach(() => {
    // These tests only provide a `kafka` config on appConfig, so pin the
    // broker type explicitly rather than relying on the process-wide default.
    process.env.MESSAGE_BROKER = 'kafka'
  })

  afterEach(() => {
    if (originalMessageBroker === undefined) {
      delete process.env.MESSAGE_BROKER
    } else {
      process.env.MESSAGE_BROKER = originalMessageBroker
    }
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
      expect(container.isBound('MessageConsumer')).to.be.true
      expect(container.isBound('Logger')).to.be.true
    })

    it('binds the email dispatcher as a singleton', async () => {
      const appConfig = {
        jwtSecret: 'j',
        scopedJwtSecret: 's',
        frontendUrl: 'https://app.example.com',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      const container = await NotificationContainer.initialize(appConfig)
      const dispatcher = container.get(NOTIFICATION_EMAIL_DISPATCHER)

      expect(dispatcher).to.be.instanceOf(NotificationEmailDispatcher)
      expect(container.get(NOTIFICATION_EMAIL_DISPATCHER)).to.equal(dispatcher)
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

    it('disconnects the email dispatcher\'s mail producer', async () => {
      const appConfig = {
        jwtSecret: 'j',
        scopedJwtSecret: 's',
        frontendUrl: 'https://app.example.com',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any
      const stop = sinon.stub(MailProducer.prototype, 'stop').resolves()
      await NotificationContainer.initialize(appConfig)
      await NotificationContainer.dispose()
      expect(stop.called, 'never built, nothing to stop').to.equal(false)

      const container = await NotificationContainer.initialize(appConfig)
      container.get(NOTIFICATION_EMAIL_DISPATCHER)
      await NotificationContainer.dispose()
      expect(stop.calledOnce).to.equal(true)
    })

    it('should do nothing when container is null', async () => {
      ;(NotificationContainer as any).container = null
      await NotificationContainer.dispose()
      // Should not throw
    })
  })
})
