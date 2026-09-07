import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  RecordsEventProducer,
  EventType,
  Event,
  NewRecordEvent,
  UpdateRecordEvent,
  DeletedRecordEvent,
  ReindexRecordEvent,
} from '../../../../src/modules/knowledge_base/services/records_events.service'

describe('RecordsEventProducer', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('EventType enum', () => {
    it('should have NewRecordEvent value', () => {
      expect(EventType.NewRecordEvent).to.equal('newRecord')
    })

    it('should have UpdateRecordEvent value', () => {
      expect(EventType.UpdateRecordEvent).to.equal('updateRecord')
    })

    it('should have DeletedRecordEvent value', () => {
      expect(EventType.DeletedRecordEvent).to.equal('deleteRecord')
    })

    it('should have ReindexRecordEvent value', () => {
      expect(EventType.ReindexRecordEvent).to.equal('reindexRecord')
    })
  })

  describe('Event interface', () => {
    it('should construct a valid Event with NewRecordEvent payload', () => {
      const payload: NewRecordEvent = {
        orgId: 'org-1',
        recordId: 'rec-1',
        recordName: 'Test',
        recordType: 'file',
        version: 1,
        signedUrlRoute: 'http://test/download',
        origin: 'upload',
        extension: '.pdf',
        mimeType: 'application/pdf',
        createdAtTimestamp: '123456',
        updatedAtTimestamp: '123456',
        sourceCreatedAtTimestamp: '123456',
      }
      const event: Event = {
        eventType: EventType.NewRecordEvent,
        timestamp: Date.now(),
        payload,
      }
      expect(event.eventType).to.equal('newRecord')
      expect(event.payload.orgId).to.equal('org-1')
    })

    it('should construct a valid Event with UpdateRecordEvent payload', () => {
      const payload: UpdateRecordEvent = {
        orgId: 'org-1',
        recordId: 'rec-1',
        version: 2,
        extension: '.docx',
        mimeType: 'application/vnd.openxmlformats',
        signedUrlRoute: 'http://test/download',
        updatedAtTimestamp: '123456',
        sourceLastModifiedTimestamp: '123456',
        virtualRecordId: 'vr-1',
        summaryDocumentId: 'sd-1',
      }
      const event: Event = {
        eventType: EventType.UpdateRecordEvent,
        timestamp: Date.now(),
        payload,
      }
      expect(event.eventType).to.equal('updateRecord')
      expect(event.payload.version).to.equal(2)
    })

    it('should construct a valid Event with DeletedRecordEvent payload', () => {
      const payload: DeletedRecordEvent = {
        orgId: 'org-1',
        recordId: 'rec-1',
        version: 1,
        extension: '.pdf',
        mimeType: 'application/pdf',
        summaryDocumentId: 'sd-1',
        virtualRecordId: 'vr-1',
      }
      const event: Event = {
        eventType: EventType.DeletedRecordEvent,
        timestamp: Date.now(),
        payload,
      }
      expect(event.eventType).to.equal('deleteRecord')
    })

    it('should construct a valid Event with ReindexRecordEvent payload', () => {
      const payload: ReindexRecordEvent = {
        orgId: 'org-1',
        recordId: 'rec-1',
        recordName: 'Test',
        recordType: 'file',
        version: 1,
        signedUrlRoute: 'http://test/download',
        origin: 'upload',
        extension: '.pdf',
        createdAtTimestamp: '123',
        updatedAtTimestamp: '456',
        sourceCreatedAtTimestamp: '789',
      }
      expect(payload.recordName).to.equal('Test')
    })
  })

  describe('RecordsEventProducer class', () => {
    it('should be a class', () => {
      expect(RecordsEventProducer).to.be.a('function')
    })

    it('should have start method on prototype', () => {
      expect(RecordsEventProducer.prototype.start).to.be.a('function')
    })

    it('should have stop method on prototype', () => {
      expect(RecordsEventProducer.prototype.stop).to.be.a('function')
    })

    it('should have publishEvent method on prototype', () => {
      expect(RecordsEventProducer.prototype.publishEvent).to.be.a('function')
    })
  })
})

describe('RecordsEventProducer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('start', () => {
    it('should call connect when not connected', async () => {
      const instance = Object.create(RecordsEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(false),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.start()
      expect(mockProducer.connect.calledOnce).to.be.true
    })
  })

  describe('stop', () => {
    it('should call disconnect when connected', async () => {
      const instance = Object.create(RecordsEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.stop()
      expect(mockProducer.disconnect.calledOnce).to.be.true
    })

    it('should not call disconnect when not connected', async () => {
      const instance = Object.create(RecordsEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(false),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.stop()
      expect(mockProducer.disconnect.called).to.be.false
    })
  })

  describe('publishEvent', () => {
    it('should publish event to records topic', async () => {
      const instance = Object.create(RecordsEventProducer.prototype)
      ;(instance as any).recordsTopic = 'record-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const event: Event = {
        eventType: EventType.NewRecordEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          recordId: 'rec-1',
          recordName: 'Test Record',
          recordType: 'file',
          version: 1,
          signedUrlRoute: 'http://test/download',
          origin: 'upload',
          extension: '.pdf',
          mimeType: 'application/pdf',
          createdAtTimestamp: '123456',
          updatedAtTimestamp: '123456',
          sourceCreatedAtTimestamp: '123456',
        },
      }

      await instance.publishEvent(event)

      expect(mockProducer.publish.calledOnce).to.be.true
      const [topic, message] = mockProducer.publish.firstCall.args
      expect(topic).to.equal('record-events')
      // Keyed by the fairness key, not the event type: keying every
      // newRecord identically would put them all on one partition the
      // moment record-events has more than one.
      expect(message.key).to.equal('org-1')
    })

    it('keys by connectorId in preference to orgId', async () => {
      // orgId separates customers but not the connectors inside one, so
      // falling back to it would put every connector in an org on the same
      // lane and undo fair scheduling between them.
      const instance = Object.create(RecordsEventProducer.prototype)
      ;(instance as any).recordsTopic = 'record-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      await instance.publishEvent({
        eventType: EventType.NewRecordEvent,
        timestamp: Date.now(),
        payload: { orgId: 'org-1', connectorId: 'conn-9' },
      } as any)

      const [, message] = mockProducer.publish.firstCall.args
      expect(message.key).to.equal('conn-9')
      expect(JSON.parse(message.value)).to.deep.include({
        eventType: EventType.NewRecordEvent,
      })
      expect(message.headers.eventType).to.equal(EventType.NewRecordEvent)
    })

    it('publishes to the connector lane stream on Redis', async () => {
      // On Redis the key is only stored as a field and selects nothing, so
      // the lane has to be the stream name or every connector goes back to
      // sharing one queue.
      const previousBroker = process.env.MESSAGE_BROKER
      const previousLanes = process.env.FAIR_SCHEDULING_LANE_COUNT
      process.env.MESSAGE_BROKER = 'redis'
      process.env.FAIR_SCHEDULING_LANE_COUNT = '8'
      try {
        const instance = Object.create(RecordsEventProducer.prototype)
        ;(instance as any).recordsTopic = 'record-events'
        const mockProducer = {
          isConnected: sinon.stub().returns(true),
          connect: sinon.stub().resolves(),
          publish: sinon.stub().resolves(),
        }
        ;(instance as any).producer = mockProducer
        instance.logger = { info: sinon.stub(), error: sinon.stub() }

        await instance.publishEvent({
          eventType: EventType.NewRecordEvent,
          timestamp: Date.now(),
          payload: { orgId: 'org-1', connectorId: 'conn-1' },
        } as any)

        const [topic] = mockProducer.publish.firstCall.args
        expect(topic).to.equal('record-events.5')
      } finally {
        if (previousBroker === undefined) delete process.env.MESSAGE_BROKER
        else process.env.MESSAGE_BROKER = previousBroker
        if (previousLanes === undefined)
          delete process.env.FAIR_SCHEDULING_LANE_COUNT
        else process.env.FAIR_SCHEDULING_LANE_COUNT = previousLanes
      }
    })

    it('should log error when publish fails', async () => {
      const instance = Object.create(RecordsEventProducer.prototype)
      ;(instance as any).recordsTopic = 'record-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().rejects(new Error('Publish failed')),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const event: Event = {
        eventType: EventType.DeletedRecordEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          recordId: 'rec-1',
          version: 1,
          extension: '.pdf',
          mimeType: 'application/pdf',
        },
      }

      await instance.publishEvent(event)
      expect(instance.logger.error.calledOnce).to.be.true
    })

    it('should publish UpdateRecordEvent', async () => {
      const instance = Object.create(RecordsEventProducer.prototype)
      ;(instance as any).recordsTopic = 'record-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const event: Event = {
        eventType: EventType.UpdateRecordEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          recordId: 'rec-1',
          version: 2,
          extension: '.docx',
          mimeType: 'application/vnd.openxmlformats',
          signedUrlRoute: 'http://test/download',
          updatedAtTimestamp: '123456',
          sourceLastModifiedTimestamp: '123456',
        },
      }

      await instance.publishEvent(event)
      expect(mockProducer.publish.calledOnce).to.be.true
      expect(instance.logger.info.calledOnce).to.be.true
    })

    it('should publish ReindexRecordEvent', async () => {
      const instance = Object.create(RecordsEventProducer.prototype)
      ;(instance as any).recordsTopic = 'record-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const event: Event = {
        eventType: EventType.ReindexRecordEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          recordId: 'rec-1',
          recordName: 'Test',
          recordType: 'file',
          version: 1,
          signedUrlRoute: 'http://test/download',
          origin: 'upload',
          extension: '.pdf',
          createdAtTimestamp: '123',
          updatedAtTimestamp: '456',
          sourceCreatedAtTimestamp: '789',
        },
      }

      await instance.publishEvent(event)
      expect(mockProducer.publish.calledOnce).to.be.true
    })
  })
})
