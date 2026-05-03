import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationProducer, EventType } from '../../../../src/modules/notification/service/notification.producer'

describe('notification/service/notification.producer', () => {
  let producer: NotificationProducer
  let mockLogger: any
  let mockProducer: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    mockProducer = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
      publish: sinon.stub().resolves(),
      publishBatch: sinon.stub().resolves(),
      healthCheck: sinon.stub().resolves(true),
    }
    producer = new NotificationProducer(
      mockProducer,
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
      mockProducer.isConnected.returns(false)
      await producer.start()
      expect(mockProducer.connect.calledOnce).to.be.true
    })

    it('should not call connect if already connected', async () => {
      mockProducer.isConnected.returns(true)
      await producer.start()
      expect(mockProducer.connect.called).to.be.false
    })
  })

  describe('stop', () => {
    it('should call disconnect if connected', async () => {
      mockProducer.isConnected.returns(true)
      await producer.stop()
      expect(mockProducer.disconnect.calledOnce).to.be.true
    })

    it('should not disconnect if not connected', async () => {
      mockProducer.isConnected.returns(false)
      await producer.stop()
      expect(mockProducer.disconnect.called).to.be.false
    })
  })

  describe('publishEvent', () => {
    it('should publish event with correct topic and format', async () => {
      const event: any = {
        eventType: EventType.NewNotificationEvent,
        timestamp: 1234567890,
        payload: { id: 'notif-1', message: 'hello' },
      }

      await producer.publishEvent(event)

      expect(mockProducer.publish.calledOnce).to.be.true
      const [topic, message] = mockProducer.publish.firstCall.args
      expect(topic).to.equal('notification')
      expect(message.key).to.equal('notif-1')
      expect(message.value).to.deep.equal(event.payload)
      expect(message.headers.eventType).to.equal('newNotification')
    })

    it('should log error when publish fails', async () => {
      mockProducer.publish.rejects(new Error('Kafka down'))
      const event: any = {
        eventType: EventType.NewNotificationEvent,
        timestamp: Date.now(),
        payload: { id: 'notif-1' },
      }

      await producer.publishEvent(event)
      expect(mockLogger.error.calledOnce).to.be.true
    })

    it('should include timestamp in headers as string', async () => {
      const event: any = {
        eventType: EventType.NewNotificationEvent,
        timestamp: 9999999999,
        payload: { id: 'notif-2' },
      }

      await producer.publishEvent(event)
      const [, message] = mockProducer.publish.firstCall.args
      expect(message.headers.timestamp).to.equal('9999999999')
    })

    it('should log success after publish', async () => {
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
