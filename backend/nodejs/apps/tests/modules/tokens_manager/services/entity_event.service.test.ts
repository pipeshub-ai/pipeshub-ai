import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  EntitiesEventProducer,
  EventType,
  SyncAction,
} from '../../../../src/modules/tokens_manager/services/entity_event.service'

describe('tokens_manager/services/entity_event.service', () => {
  let producer: EntitiesEventProducer
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
    producer = new EntitiesEventProducer(mockProducer, mockLogger)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('EventType enum', () => {
    it('should have AppEnabledEvent', () => {
      expect(EventType.AppEnabledEvent).to.equal('appEnabled')
    })

    it('should have AppDisabledEvent', () => {
      expect(EventType.AppDisabledEvent).to.equal('appDisabled')
    })
  })

  describe('SyncAction enum', () => {
    it('should have correct values', () => {
      expect(SyncAction.None).to.equal('none')
      expect(SyncAction.Immediate).to.equal('immediate')
      expect(SyncAction.Scheduled).to.equal('scheduled')
    })
  })

  describe('constructor', () => {
    it('should create instance with correct topic', () => {
      expect(producer).to.be.instanceOf(EntitiesEventProducer)
      expect((producer as any).topic).to.equal('entity-events')
    })
  })

  describe('publishEvent', () => {
    it('should publish event with correct key and headers', async () => {
      const event: any = {
        eventType: EventType.AppEnabledEvent,
        timestamp: 1234567890,
        payload: {
          orgId: 'org-1',
          appGroup: 'google',
          appGroupId: 'gw-1',
          apps: ['drive'],
          syncAction: SyncAction.Immediate,
        },
      }

      await producer.publishEvent(event)

      expect(mockProducer.publish.calledOnce).to.be.true
      const [topic, message] = mockProducer.publish.firstCall.args
      expect(topic).to.equal('entity-events')
      expect(message.key).to.equal(EventType.AppEnabledEvent)
      expect(message.headers.eventType).to.equal(EventType.AppEnabledEvent)
      expect(message.headers.timestamp).to.equal('1234567890')
    })

    it('should log error if publish fails', async () => {
      mockProducer.publish.rejects(new Error('Kafka error'))

      const event: any = {
        eventType: EventType.AppEnabledEvent,
        timestamp: Date.now(),
        payload: {},
      }

      await producer.publishEvent(event)

      expect(mockLogger.error.calledOnce).to.be.true
    })
  })

  describe('start', () => {
    it('should call connect if not connected', async () => {
      mockProducer.isConnected.returns(false)
      await producer.start()
      expect(mockProducer.connect.calledOnce).to.be.true
    })
  })

  describe('stop', () => {
    it('should call disconnect if connected', async () => {
      mockProducer.isConnected.returns(true)
      await producer.stop()
      expect(mockProducer.disconnect.calledOnce).to.be.true
    })
  })
})
