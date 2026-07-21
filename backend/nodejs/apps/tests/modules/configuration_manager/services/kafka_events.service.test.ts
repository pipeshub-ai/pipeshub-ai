import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  SyncEventProducer,
  EntitiesEventProducer,
  AiConfigEventProducer,
  EventType,
  SyncAction,
} from '../../../../src/modules/configuration_manager/services/kafka_events.service'

function makeMockProducer(connected = false) {
  return {
    isConnected: sinon.stub().returns(connected),
    connect: sinon.stub().resolves(),
    disconnect: sinon.stub().resolves(),
    publish: sinon.stub().resolves(),
    publishBatch: sinon.stub().resolves(),
    healthCheck: sinon.stub().resolves(true),
  }
}

function makeLogger() {
  return {
    info: sinon.stub(),
    error: sinon.stub(),
    warn: sinon.stub(),
    debug: sinon.stub(),
  }
}

function makeSampleEvent() {
  return {
    eventType: EventType.LLMConfiguredEvent,
    timestamp: Date.now(),
    payload: { credentialsRoute: '/credentials' },
  }
}

describe('kafka_events.service', () => {
  afterEach(() => {
    sinon.restore()
  })

  // ===========================================================================
  // SyncEventProducer
  // ===========================================================================
  describe('SyncEventProducer', () => {
    let producer: SyncEventProducer
    let mockProducer: ReturnType<typeof makeMockProducer>
    let logger: ReturnType<typeof makeLogger>

    beforeEach(() => {
      mockProducer = makeMockProducer(false)
      logger = makeLogger()
      producer = Object.create(SyncEventProducer.prototype)
      ;(producer as any).producer = mockProducer
      ;(producer as any).logger = logger
      ;(producer as any).topic = 'sync-events'
    })

    describe('start', () => {
      it('should connect when not already connected', async () => {
        mockProducer.isConnected.returns(false)
        await producer.start()
        expect(mockProducer.connect.calledOnce).to.be.true
      })

      it('should not connect when already connected', async () => {
        mockProducer.isConnected.returns(true)
        await producer.start()
        expect(mockProducer.connect.called).to.be.false
      })
    })

    describe('stop', () => {
      it('should disconnect when connected', async () => {
        mockProducer.isConnected.returns(true)
        await producer.stop()
        expect(mockProducer.disconnect.calledOnce).to.be.true
      })

      it('should not disconnect when not connected', async () => {
        mockProducer.isConnected.returns(false)
        await producer.stop()
        expect(mockProducer.disconnect.called).to.be.false
      })
    })

    describe('isConnected', () => {
      it('should return true when producer is connected', () => {
        mockProducer.isConnected.returns(true)
        expect(producer.isConnected()).to.be.true
      })

      it('should return false when producer is not connected', () => {
        mockProducer.isConnected.returns(false)
        expect(producer.isConnected()).to.be.false
      })
    })

    describe('publishEvent', () => {
      it('should publish a LLMConfiguredEvent', async () => {
        await producer.publishEvent(makeSampleEvent())
        expect(mockProducer.publish.calledOnce).to.be.true
        const [topic, message] = mockProducer.publish.firstCall.args
        expect(topic).to.equal('sync-events')
        expect(message.key).to.equal(EventType.LLMConfiguredEvent)
        expect(JSON.parse(message.value).eventType).to.equal(EventType.LLMConfiguredEvent)
        expect(logger.info.calledOnce).to.be.true
      })

      it('should publish a ConnectorPublicUrlChangedEvent', async () => {
        const event = {
          eventType: EventType.ConnectorPublicUrlChangedEvent,
          timestamp: Date.now(),
          payload: { orgId: 'org-1', url: 'https://new-url.example.com' },
        }
        await producer.publishEvent(event)
        expect(mockProducer.publish.calledOnce).to.be.true
        expect(logger.info.calledOnce).to.be.true
      })

      it('should log error when publish throws', async () => {
        mockProducer.publish.rejects(new Error('Kafka unavailable'))
        await producer.publishEvent(makeSampleEvent())
        expect(logger.error.calledOnce).to.be.true
        expect(logger.info.called).to.be.false
      })

      it('should include timestamp in message headers', async () => {
        const event = makeSampleEvent()
        await producer.publishEvent(event)
        const [, message] = mockProducer.publish.firstCall.args
        expect(message.headers.eventType).to.equal(EventType.LLMConfiguredEvent)
        expect(message.headers.timestamp).to.equal(event.timestamp.toString())
      })

      it('should publish AppEnabledEvent', async () => {
        const event = {
          eventType: EventType.AppEnabledEvent,
          timestamp: Date.now(),
          payload: {
            orgId: 'org-1',
            appGroup: 'google',
            appGroupId: 'grp-1',
            apps: ['gmail'],
            syncAction: SyncAction.Immediate,
          },
        }
        await producer.publishEvent(event)
        expect(mockProducer.publish.calledOnce).to.be.true
      })

      it('should publish AppDisabledEvent', async () => {
        const event = {
          eventType: EventType.AppDisabledEvent,
          timestamp: Date.now(),
          payload: {
            orgId: 'org-1',
            appGroup: 'google',
            appGroupId: 'grp-1',
            apps: ['gmail'],
          },
        }
        await producer.publishEvent(event)
        expect(mockProducer.publish.calledOnce).to.be.true
      })

      it('should publish GmailUpdatesEnabledEvent', async () => {
        const event = {
          eventType: EventType.GmailUpdatesEnabledEvent,
          timestamp: Date.now(),
          payload: { orgId: 'org-1', topicName: 'gmail-topic' },
        }
        await producer.publishEvent(event)
        expect(mockProducer.publish.calledOnce).to.be.true
      })

      it('should publish GmailUpdatesDisabledEvent', async () => {
        const event = {
          eventType: EventType.GmailUpdatesDisabledEvent,
          timestamp: Date.now(),
          payload: { orgId: 'org-1' },
        }
        await producer.publishEvent(event)
        expect(mockProducer.publish.calledOnce).to.be.true
      })
    })
  })

  // ===========================================================================
  // EntitiesEventProducer
  // ===========================================================================
  describe('EntitiesEventProducer', () => {
    let producer: EntitiesEventProducer
    let mockProducer: ReturnType<typeof makeMockProducer>
    let logger: ReturnType<typeof makeLogger>

    beforeEach(() => {
      mockProducer = makeMockProducer(false)
      logger = makeLogger()
      producer = Object.create(EntitiesEventProducer.prototype)
      ;(producer as any).producer = mockProducer
      ;(producer as any).logger = logger
      ;(producer as any).topic = 'entity-events'
    })

    describe('start', () => {
      it('should connect when not already connected', async () => {
        mockProducer.isConnected.returns(false)
        await producer.start()
        expect(mockProducer.connect.calledOnce).to.be.true
      })

      it('should not connect when already connected', async () => {
        mockProducer.isConnected.returns(true)
        await producer.start()
        expect(mockProducer.connect.called).to.be.false
      })
    })

    describe('stop', () => {
      it('should disconnect when connected', async () => {
        mockProducer.isConnected.returns(true)
        await producer.stop()
        expect(mockProducer.disconnect.calledOnce).to.be.true
      })

      it('should not disconnect when not connected', async () => {
        mockProducer.isConnected.returns(false)
        await producer.stop()
        expect(mockProducer.disconnect.called).to.be.false
      })
    })

    describe('isConnected', () => {
      it('should return the connected state from the underlying producer', () => {
        mockProducer.isConnected.returns(true)
        expect(producer.isConnected()).to.be.true

        mockProducer.isConnected.returns(false)
        expect(producer.isConnected()).to.be.false
      })
    })

    describe('publishEvent', () => {
      it('should publish event to entity-events topic', async () => {
        const event = {
          eventType: EventType.EmbeddingModelConfiguredEvent,
          timestamp: Date.now(),
          payload: { credentialsRoute: '/embedding-credentials' },
        }
        await producer.publishEvent(event)
        expect(mockProducer.publish.calledOnce).to.be.true
        const [topic, message] = mockProducer.publish.firstCall.args
        expect(topic).to.equal('entity-events')
        expect(message.key).to.equal(EventType.EmbeddingModelConfiguredEvent)
        expect(logger.info.calledOnce).to.be.true
      })

      it('should log error when publish fails', async () => {
        mockProducer.publish.rejects(new Error('Broker error'))
        await producer.publishEvent(makeSampleEvent())
        expect(logger.error.calledOnce).to.be.true
      })
    })
  })

  // ===========================================================================
  // AiConfigEventProducer
  // ===========================================================================
  describe('AiConfigEventProducer', () => {
    let producer: AiConfigEventProducer
    let mockProducer: ReturnType<typeof makeMockProducer>
    let logger: ReturnType<typeof makeLogger>

    beforeEach(() => {
      mockProducer = makeMockProducer(false)
      logger = makeLogger()
      producer = Object.create(AiConfigEventProducer.prototype)
      ;(producer as any).producer = mockProducer
      ;(producer as any).logger = logger
      ;(producer as any).topic = 'ai-config-events'
    })

    describe('start', () => {
      it('should connect when not already connected', async () => {
        mockProducer.isConnected.returns(false)
        await producer.start()
        expect(mockProducer.connect.calledOnce).to.be.true
      })

      it('should not connect when already connected', async () => {
        mockProducer.isConnected.returns(true)
        await producer.start()
        expect(mockProducer.connect.called).to.be.false
      })
    })

    describe('stop', () => {
      it('should disconnect when connected', async () => {
        mockProducer.isConnected.returns(true)
        await producer.stop()
        expect(mockProducer.disconnect.calledOnce).to.be.true
      })

      it('should not disconnect when not connected', async () => {
        mockProducer.isConnected.returns(false)
        await producer.stop()
        expect(mockProducer.disconnect.called).to.be.false
      })
    })

    describe('isConnected', () => {
      it('should delegate to producer.isConnected', () => {
        mockProducer.isConnected.returns(true)
        expect(producer.isConnected()).to.be.true
      })
    })

    describe('publishEvent', () => {
      it('should publish event to ai-config-events topic', async () => {
        const event = {
          eventType: EventType.LLMConfiguredEvent,
          timestamp: Date.now(),
          payload: { credentialsRoute: '/llm-credentials' },
        }
        await producer.publishEvent(event)
        expect(mockProducer.publish.calledOnce).to.be.true
        const [topic, message] = mockProducer.publish.firstCall.args
        expect(topic).to.equal('ai-config-events')
        expect(JSON.parse(message.value).payload).to.deep.equal({ credentialsRoute: '/llm-credentials' })
        expect(logger.info.calledOnce).to.be.true
      })

      it('should log error and not rethrow when publish fails', async () => {
        mockProducer.publish.rejects(new Error('Network error'))
        // Should not throw
        await producer.publishEvent(makeSampleEvent())
        expect(logger.error.calledOnce).to.be.true
      })

      it('should include correct headers in published message', async () => {
        const ts = Date.now()
        const event = {
          eventType: EventType.EmbeddingModelConfiguredEvent,
          timestamp: ts,
          payload: { credentialsRoute: '/emb' },
        }
        await producer.publishEvent(event)
        const [, message] = mockProducer.publish.firstCall.args
        expect(message.headers.eventType).to.equal(EventType.EmbeddingModelConfiguredEvent)
        expect(message.headers.timestamp).to.equal(ts.toString())
      })
    })
  })
})
