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
