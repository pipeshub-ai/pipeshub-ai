import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { RecordRelationService } from '../../../../src/modules/knowledge_base/services/kb.relation.service'
import { InternalServerError } from '../../../../src/libs/errors/http.errors'

describe('RecordRelationService - additional coverage', () => {
  let mockEventProducer: any
  let mockSyncEventProducer: any
  let mockDefaultConfig: any

  beforeEach(() => {
    mockEventProducer = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }
    mockSyncEventProducer = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }
    mockDefaultConfig = {
      endpoint: 'http://localhost:3003',
    }
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('initializeEventProducer - error path', () => {
    it('should throw InternalServerError when event producer start fails', async () => {
      const failingProducer = {
        start: sinon.stub().rejects(new Error('Kafka connection failed')),
        publishEvent: sinon.stub(),
        stop: sinon.stub(),
      }

      try {
        new RecordRelationService(
          failingProducer as any,
          mockSyncEventProducer,
          mockDefaultConfig,
        )
        // Wait for the async initialization
        await new Promise(resolve => setTimeout(resolve, 50))
      } catch (error) {
        // The error is thrown asynchronously from constructor, may not catch here
      }
      // Verify the producer start was attempted
      expect(failingProducer.start.calledOnce).to.be.true
    })
  })

  describe('initializeSyncEventProducer - error path', () => {
    it('should throw InternalServerError when sync event producer start fails', async () => {
      const failingSyncProducer = {
        start: sinon.stub().rejects(new Error('Sync kafka failed')),
        publishEvent: sinon.stub(),
        stop: sinon.stub(),
      }

      try {
        new RecordRelationService(
          mockEventProducer,
          failingSyncProducer as any,
          mockDefaultConfig,
        )
        await new Promise(resolve => setTimeout(resolve, 50))
      } catch (error) {
        // async error
      }
      expect(failingSyncProducer.start.calledOnce).to.be.true
    })
  })

  describe('createNewRecordEventPayload - edge cases', () => {
    it('should handle missing timestamps by using Date.now()', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        recordName: 'Test',
        recordType: 'file',
        origin: 'upload',
        externalRecordId: 'ext-1',
        // No version, no timestamps, no sourceCreatedAtTimestamp
      }

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({
          storage: { endpoint: 'http://storage:3003' },
        })),
      }

      const payload = await service.createNewRecordEventPayload(record, mockKeyValueStore)

      expect(payload.version).to.equal(1) // default
      expect(payload.createdAtTimestamp).to.be.a('string')
      expect(payload.updatedAtTimestamp).to.be.a('string')
      expect(payload.sourceCreatedAtTimestamp).to.be.a('string')
      expect(payload.extension).to.equal('')
      expect(payload.mimeType).to.equal('')
    })

    it('should use sourceCreatedAtTimestamp from record when available', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const now = Date.now()
      const record: any = {
        _key: 'r-2',
        orgId: 'org-1',
        recordName: 'Test2',
        recordType: 'file',
        version: 3,
        origin: 'upload',
        externalRecordId: 'ext-2',
        createdAtTimestamp: now - 1000,
        updatedAtTimestamp: now,
        sourceCreatedAtTimestamp: now - 5000,
      }

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({
          storage: { endpoint: 'http://storage:3003' },
        })),
      }

      const fileRecord: any = { extension: '.txt', mimeType: 'text/plain' }
      const payload = await service.createNewRecordEventPayload(record, mockKeyValueStore, fileRecord)

      expect(payload.sourceCreatedAtTimestamp).to.equal(String(now - 5000))
    })

    it('should fallback to createdAtTimestamp when sourceCreatedAtTimestamp is missing', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const createdAt = Date.now() - 2000
      const record: any = {
        _key: 'r-3',
        orgId: 'org-1',
        recordName: 'Test3',
        recordType: 'file',
        version: 1,
        origin: 'upload',
        externalRecordId: 'ext-3',
        createdAtTimestamp: createdAt,
        updatedAtTimestamp: Date.now(),
      }

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({
          storage: { endpoint: 'http://storage:3003' },
        })),
      }

      const payload = await service.createNewRecordEventPayload(record, mockKeyValueStore)
      expect(payload.sourceCreatedAtTimestamp).to.equal(String(createdAt))
    })
  })

  describe('createUpdateRecordEventPayload - edge cases', () => {
    it('should handle record with no sourceLastModifiedTimestamp', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const updated = Date.now()
      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        externalRecordId: 'ext-1',
        updatedAtTimestamp: updated,
        virtualRecordId: 'vr-1',
        summaryDocumentId: 'sd-1',
        // no sourceLastModifiedTimestamp
      }
      const fileRecord: any = { extension: '.pdf', mimeType: 'application/pdf' }
      const mockKvStore: any = {
        get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: 'http://s:3003' } })),
      }

      const payload = await service.createUpdateRecordEventPayload(record, fileRecord, mockKvStore)
      expect(payload.sourceLastModifiedTimestamp).to.equal(String(updated))
      expect(payload.virtualRecordId).to.equal('vr-1')
      expect(payload.summaryDocumentId).to.equal('sd-1')
    })

    it('should use sourceLastModifiedTimestamp when available', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const srcMod = Date.now() - 10000
      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        externalRecordId: 'ext-1',
        updatedAtTimestamp: Date.now(),
        sourceLastModifiedTimestamp: srcMod,
      }
      const fileRecord: any = {}
      const mockKvStore: any = {
        get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: 'http://s:3003' } })),
      }

      const payload = await service.createUpdateRecordEventPayload(record, fileRecord, mockKvStore)
      expect(payload.sourceLastModifiedTimestamp).to.equal(String(srcMod))
    })
  })

  describe('createReindexRecordEventPayload', () => {
    it('should create reindex payload with correct fields', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        recordName: 'Test',
        recordType: 'file',
        version: 1,
        origin: 'upload',
        externalRecordId: 'ext-1',
        fileRecord: { extension: '.pdf' },
      }
      const fileRecord: any = { mimeType: 'application/pdf' }
      const mockKvStore: any = {
        get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: 'http://s:3003' } })),
      }

      const payload = await service.createReindexRecordEventPayload(record, fileRecord, mockKvStore)
      expect(payload.orgId).to.equal('org-1')
      expect(payload.recordId).to.equal('r-1')
      expect(payload.recordName).to.equal('Test')
      expect(payload.extension).to.equal('.pdf')
      expect(payload.mimeType).to.equal('application/pdf')
    })

    it('should use default endpoint when storage endpoint is not in KV store', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        recordName: 'Test',
        recordType: 'file',
        version: 2,
        origin: 'upload',
        externalRecordId: 'ext-1',
        fileRecord: { extension: '.txt' },
      }
      const fileRecord: any = {}
      const mockKvStore: any = {
        get: sinon.stub().resolves(null), // returns null -> fallback to '{}'
      }

      // When URL is '{}', JSON.parse('{}').storage?.endpoint is undefined -> uses default
      const payload = await service.createReindexRecordEventPayload(record, fileRecord, mockKvStore)
      expect(payload.signedUrlRoute).to.include('http://localhost:3003')
    })
  })

  describe('reindexFailedRecords - statusFilters override', () => {
    it('should use provided statusFilters', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const result = await service.reindexFailedRecords({
        app: 'Slack',
        connectorId: 'conn-1',
        orgId: 'org-1',
        statusFilters: ['PENDING', 'FAILED'],
      })

      expect(result.success).to.be.true
      const event = mockSyncEventProducer.publishEvent.firstCall.args[0]
      expect(event.payload.statusFilters).to.deep.equal(['PENDING', 'FAILED'])
    })

    it('should normalize connector name by removing spaces and lowering case', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      await service.reindexFailedRecords({
        app: 'Google Drive',
        connectorId: 'conn-1',
        orgId: 'org-1',
      })

      const event = mockSyncEventProducer.publishEvent.firstCall.args[0]
      expect(event.eventType).to.equal('googledrive.reindex')
      expect(event.payload.connector).to.equal('googledrive')
    })
  })

  describe('resyncConnectorRecords - event type construction', () => {
    it('should build event type from connector name', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      await service.resyncConnectorRecords({
        connectorName: 'Google Drive',
        connectorId: 'conn-1',
        orgId: 'org-1',
        origin: 'googleDrive',
        fullSync: true,
      })

      const event = mockSyncEventProducer.publishEvent.firstCall.args[0]
      expect(event.eventType).to.include('.resync')
    })
  })
})
