import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationProducer, EventType } from '../../../../src/modules/notification/service/notification.producer'

describe('notification/service/notification.producer', () => {
  let producer: NotificationProducer
  let mockLogger: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    producer = new NotificationProducer(
      { brokers: ['localhost:9092'] },
      mockLogger,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('EventType enum', () => {
    it('should have NewNotificationEvent', () => {
      expect(EventType.NewNotificationEvent).to.equal('newNotification')
    })
  })

  describe('constructor', () => {
    it('should set topic to notification', () => {
      expect((producer as any).topic).to.equal('notification')
    })
  })

  describe('start', () => {
    it('should call connect if not connected', async () => {
      sinon.stub(producer, 'isConnected').returns(false)
      const connectStub = sinon.stub(producer, 'connect' as any).resolves()
      await producer.start()
      expect(connectStub.calledOnce).to.be.true
    })

    it('should not call connect if already connected', async () => {
      sinon.stub(producer, 'isConnected').returns(true)
      const connectStub = sinon.stub(producer, 'connect' as any).resolves()
      await producer.start()
      expect(connectStub.called).to.be.false
    })
  })

  describe('stop', () => {
    it('should call disconnect if connected', async () => {
      sinon.stub(producer, 'isConnected').returns(true)
      const disconnectStub = sinon.stub(producer, 'disconnect' as any).resolves()
      await producer.stop()
      expect(disconnectStub.calledOnce).to.be.true
    })

    it('should not disconnect if not connected', async () => {
      sinon.stub(producer, 'isConnected').returns(false)
      const disconnectStub = sinon.stub(producer, 'disconnect' as any).resolves()
      await producer.stop()
      expect(disconnectStub.called).to.be.false
    })
  })

  describe('publishEvent', () => {
    it('should publish event with correct topic and format', async () => {
      const publishStub = sinon.stub(producer, 'publish' as any).resolves()
      const event: any = {
        eventType: EventType.NewNotificationEvent,
        timestamp: 1234567890,
        payload: { id: 'notif-1', message: 'hello' },
      }

      await producer.publishEvent(event)

      expect(publishStub.calledOnce).to.be.true
      const [topic, message] = publishStub.firstCall.args
      expect(topic).to.equal('notification')
      expect(message.key).to.equal('notif-1')
      expect(message.value).to.deep.equal(event.payload)
      expect(message.headers.eventType).to.equal('newNotification')
    })

    it('should log error when publish fails', async () => {
      sinon.stub(producer, 'publish' as any).rejects(new Error('Kafka down'))
      const event: any = {
        eventType: EventType.NewNotificationEvent,
        timestamp: Date.now(),
        payload: { id: 'notif-1' },
      }

      await producer.publishEvent(event)
      expect(mockLogger.error.calledOnce).to.be.true
    })

    it('should include timestamp in headers as string', async () => {
      const publishStub = sinon.stub(producer, 'publish' as any).resolves()
      const event: any = {
        eventType: EventType.NewNotificationEvent,
        timestamp: 9999999999,
        payload: { id: 'notif-2' },
      }

      await producer.publishEvent(event)
      const [, message] = publishStub.firstCall.args
      expect(message.headers.timestamp).to.equal('9999999999')
    })

    it('should log success after publish', async () => {
      sinon.stub(producer, 'publish' as any).resolves()
      const event: any = {
        eventType: EventType.NewNotificationEvent,
        timestamp: Date.now(),
        payload: { id: 'notif-3' },
      }

      await producer.publishEvent(event)
      expect(mockLogger.info.calledOnce).to.be.true
    })
  })
})
