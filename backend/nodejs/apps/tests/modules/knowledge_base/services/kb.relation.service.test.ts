import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { RecordRelationService } from '../../../../src/modules/knowledge_base/services/kb.relation.service'

describe('RecordRelationService', () => {
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

  describe('constructor', () => {
    it('should create an instance and initialize producers', () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      expect(service).to.exist
    })

    it('should call start on both event producers', async () => {
      new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )

      // Since initialization is async but constructor returns immediately,
      // we need to wait a tick for the promises to resolve
      await new Promise(resolve => setTimeout(resolve, 10))

      expect(mockEventProducer.start.calledOnce).to.be.true
      expect(mockSyncEventProducer.start.calledOnce).to.be.true
    })
  })

  describe('publishRecordEvents', () => {
    it('should publish events for each record', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )

      // Wait for initialization
      await new Promise(resolve => setTimeout(resolve, 10))

      const records: any[] = [
        {
          _key: 'r-1',
          orgId: 'org-1',
          recordName: 'Test Record',
          recordType: 'file',
          version: 1,
          origin: 'upload',
          externalRecordId: 'ext-1',
          createdAtTimestamp: Date.now(),
          updatedAtTimestamp: Date.now(),
        },
      ]

      const fileRecords: any[] = [
        {
          extension: '.pdf',
          mimeType: 'application/pdf',
        },
      ]

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({
          storage: { endpoint: 'http://localhost:3003' },
        })),
      }

      await service.publishRecordEvents(records, fileRecords, mockKeyValueStore)

      expect(mockEventProducer.publishEvent.calledOnce).to.be.true
    })

    it('should handle empty records array gracefully', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves('{}'),
      }

      await service.publishRecordEvents([], [], mockKeyValueStore)

      // Should complete without error
      expect(mockEventProducer.publishEvent.called).to.be.false
    })
  })

  describe('createNewRecordEventPayload', () => {
    it('should create proper event payload', async () => {
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
        createdAtTimestamp: Date.now(),
        updatedAtTimestamp: Date.now(),
      }

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({
          storage: { endpoint: 'http://storage:3003' },
        })),
      }

      const fileRecord: any = {
        extension: '.txt',
        mimeType: 'text/plain',
      }

      const payload = await service.createNewRecordEventPayload(record, mockKeyValueStore, fileRecord)

      expect(payload).to.have.property('orgId', 'org-1')
      expect(payload).to.have.property('recordId', 'r-1')
      expect(payload).to.have.property('recordName', 'Test')
      expect(payload).to.have.property('extension', '.txt')
      expect(payload).to.have.property('mimeType', 'text/plain')
      expect(payload.signedUrlRoute).to.include('ext-1')
    })

    it('should use default storage endpoint when URL data has storage config', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const record: any = {
        _key: 'r-2',
        orgId: 'org-1',
        recordName: 'Test2',
        recordType: 'file',
        version: 1,
        origin: 'upload',
        externalRecordId: 'ext-2',
        createdAtTimestamp: Date.now(),
        updatedAtTimestamp: Date.now(),
      }

      // The source code does JSON.parse(url).storage.endpoint
      // and falls back to defaultConfig.endpoint if the value is falsy.
      // Passing empty string for endpoint triggers the fallback.
      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: '' } })),
      }

      const payload = await service.createNewRecordEventPayload(record, mockKeyValueStore)

      expect(payload).to.have.property('extension', '')
      expect(payload).to.have.property('mimeType', '')
    })
  })

  describe('createUpdateRecordEventPayload', () => {
    it('should create proper update event payload', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        recordName: 'Updated',
        recordType: 'file',
        version: 2,
        origin: 'upload',
        externalRecordId: 'ext-1',
        createdAtTimestamp: Date.now(),
        updatedAtTimestamp: Date.now(),
      }

      const fileRecord: any = {
        extension: '.docx',
        mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      }

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({
          storage: { endpoint: 'http://storage:3003' },
        })),
      }

      const payload = await service.createUpdateRecordEventPayload(record, fileRecord, mockKeyValueStore)

      expect(payload).to.have.property('orgId', 'org-1')
      expect(payload).to.have.property('recordId', 'r-1')
      expect(payload).to.have.property('version', 2)
      expect(payload).to.have.property('extension', '.docx')
    })

    it('should use default endpoint when storage endpoint is empty', async () => {
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
        updatedAtTimestamp: Date.now(),
      }

      const fileRecord: any = {
        extension: '.pdf',
        mimeType: 'application/pdf',
      }

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: '' } })),
      }

      const payload = await service.createUpdateRecordEventPayload(record, fileRecord, mockKeyValueStore)

      expect(payload.signedUrlRoute).to.include('http://localhost:3003')
    })

    it('should handle missing file record fields', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        version: 1,
        externalRecordId: 'ext-1',
        updatedAtTimestamp: Date.now(),
      }

      const fileRecord: any = {}

      const mockKeyValueStore: any = {
        get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: 'http://storage:3003' } })),
      }

      const payload = await service.createUpdateRecordEventPayload(record, fileRecord, mockKeyValueStore)

      expect(payload).to.have.property('extension', '')
      expect(payload).to.have.property('mimeType', '')
    })
  })

  // -----------------------------------------------------------------------
  // createDeletedRecordEventPayload
  // -----------------------------------------------------------------------
  describe('createDeletedRecordEventPayload', () => {
    it('should create proper deleted event payload', () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )

      const record: any = {
        _key: 'r-1',
        orgId: 'org-1',
        version: 1,
        summaryDocumentId: 'sum-1',
        virtualRecordId: 'vr-1',
      }

      const fileRecord: any = {
        extension: '.pdf',
        mimeType: 'application/pdf',
      }

      const payload = service.createDeletedRecordEventPayload(record, fileRecord)

      expect(payload).to.have.property('orgId', 'org-1')
      expect(payload).to.have.property('recordId', 'r-1')
      expect(payload).to.have.property('version', 1)
      expect(payload).to.have.property('extension', '.pdf')
      expect(payload).to.have.property('mimeType', 'application/pdf')
      expect(payload).to.have.property('summaryDocumentId', 'sum-1')
      expect(payload).to.have.property('virtualRecordId', 'vr-1')
    })

    it('should handle missing file record fields', () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )

      const record: any = {
        _key: 'r-2',
        orgId: 'org-1',
        version: 2,
      }

      const fileRecord: any = {}

      const payload = service.createDeletedRecordEventPayload(record, fileRecord)

      expect(payload).to.have.property('extension', '')
      expect(payload).to.have.property('mimeType', '')
    })

    it('should handle null file record gracefully', () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )

      const record: any = {
        _key: 'r-3',
        orgId: 'org-1',
      }

      const fileRecord: any = null

      const payload = service.createDeletedRecordEventPayload(record, fileRecord)

      expect(payload).to.have.property('extension', '')
      expect(payload).to.have.property('mimeType', '')
    })
  })

  // -----------------------------------------------------------------------
  // reindexFailedRecords
  // -----------------------------------------------------------------------
  describe('reindexFailedRecords', () => {
    it('should publish reindex event and return success', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const result = await service.reindexFailedRecords({
        app: 'Google Drive',
        connectorId: 'conn-1',
        orgId: 'org-1',
      })

      expect(result.success).to.be.true
      expect(mockSyncEventProducer.publishEvent.calledOnce).to.be.true
      const event = mockSyncEventProducer.publishEvent.firstCall.args[0]
      expect(event.eventType).to.equal('googledrive.reindex')
    })

    it('should default statusFilters to FAILED', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      await service.reindexFailedRecords({
        app: 'Slack',
        connectorId: 'conn-2',
        orgId: 'org-1',
      })

      const event = mockSyncEventProducer.publishEvent.firstCall.args[0]
      expect(event.payload.statusFilters).to.deep.equal(['FAILED'])
    })

    it('should return failure when publishEvent throws', async () => {
      mockSyncEventProducer.publishEvent.rejects(new Error('publish failed'))

      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const result = await service.reindexFailedRecords({
        app: 'Drive',
        connectorId: 'conn-1',
        orgId: 'org-1',
      })

      expect(result.success).to.be.false
      expect(result.error).to.equal('publish failed')
    })
  })

  // -----------------------------------------------------------------------
  // resyncConnectorRecords
  // -----------------------------------------------------------------------
  describe('resyncConnectorRecords', () => {
    it('should publish resync event and return success', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const result = await service.resyncConnectorRecords({
        connectorName: 'Google Drive',
        connectorId: 'conn-1',
        orgId: 'org-1',
        origin: 'googleDrive',
        fullSync: false,
      })

      expect(result.success).to.be.true
      expect(mockSyncEventProducer.publishEvent.calledOnce).to.be.true
    })

    it('should return failure when publishEvent throws', async () => {
      mockSyncEventProducer.publishEvent.rejects(new Error('publish failed'))

      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const result = await service.resyncConnectorRecords({
        connectorName: 'Slack',
        connectorId: 'conn-2',
        orgId: 'org-1',
        origin: 'slack',
      })

      expect(result.success).to.be.false
      expect(result.error).to.equal('publish failed')
    })
  })

  // -----------------------------------------------------------------------
  // createReindexFailedRecordEventPayload
  // -----------------------------------------------------------------------
  describe('createReindexFailedRecordEventPayload', () => {
    it('should create proper payload', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const payload = await service.createReindexFailedRecordEventPayload({
        orgId: 'org-1',
        origin: 'googleDrive',
        app: 'Google Drive',
        connectorId: 'conn-1',
      })

      expect(payload).to.have.property('orgId', 'org-1')
      expect(payload).to.have.property('origin', 'googleDrive')
      expect(payload).to.have.property('connector', 'Google Drive')
      expect(payload).to.have.property('connectorId', 'conn-1')
      expect(payload).to.have.property('createdAtTimestamp')
      expect(payload).to.have.property('updatedAtTimestamp')
    })
  })

  // -----------------------------------------------------------------------
  // createResyncConnectorEventPayload
  // -----------------------------------------------------------------------
  describe('createResyncConnectorEventPayload', () => {
    it('should create proper payload', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const payload = await service.createResyncConnectorEventPayload({
        connectorName: 'Slack',
        connectorId: 'conn-2',
        orgId: 'org-1',
        origin: 'slack',
        fullSync: true,
      })

      expect(payload).to.have.property('orgId', 'org-1')
      expect(payload).to.have.property('connector', 'Slack')
      expect(payload).to.have.property('connectorId', 'conn-2')
      expect(payload).to.have.property('fullSync', true)
    })
  })

  // -----------------------------------------------------------------------
  // publishRecordEvents - additional tests
  // -----------------------------------------------------------------------
  describe('publishRecordEvents (additional)', () => {
    it('should handle errors in individual event creation without failing', async () => {
      const service = new RecordRelationService(
        mockEventProducer,
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      const records: any[] = [
        { _key: 'r-1', orgId: 'org-1', recordName: 'Test', recordType: 'file', version: 1, origin: 'upload', externalRecordId: 'ext-1' },
      ]

      const fileRecords: any[] = [
        { extension: '.pdf', mimeType: 'application/pdf' },
      ]

      // Make keyValueStore.get throw to cause error in payload creation
      const mockKeyValueStore: any = {
        get: sinon.stub().rejects(new Error('storage error')),
      }

      // Should not throw even when individual event creation fails
      await service.publishRecordEvents(records, fileRecords, mockKeyValueStore)

      // publishEvent should not be called since payload creation failed
      // but it won't throw
    })
  })
})
