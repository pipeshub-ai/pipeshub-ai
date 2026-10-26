import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  RecordRelationService,
  RESYNC_NOT_QUEUED_MESSAGE,
} from '../../../../src/modules/knowledge_base/services/kb.relation.service'
import { InternalServerError, ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'

describe('RecordRelationService', () => {
  let mockSyncEventProducer: any
  let mockDefaultConfig: any

  beforeEach(() => {
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
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      expect(service).to.exist
    })

    it('should start the sync event producer', async () => {
      new RecordRelationService(
        mockSyncEventProducer,
        mockDefaultConfig,
      )

      // Since initialization is async but constructor returns immediately,
      // we need to wait a tick for the promises to resolve
      await new Promise(resolve => setTimeout(resolve, 10))

      expect(mockSyncEventProducer.start.calledOnce).to.be.true
    })
  })

  describe('createUpdateRecordEventPayload', () => {
    it('should create proper update event payload', async () => {
      const service = new RecordRelationService(
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
  // resyncConnectorRecords
  // -----------------------------------------------------------------------
  describe('resyncConnectorRecords', () => {
    it('should publish resync event and return success', async () => {
      const service = new RecordRelationService(
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

    it('throws a 503 with a plain message when the event cannot be published', async () => {
      mockSyncEventProducer.publishEvent.rejects(new Error('publish failed'))

      const service = new RecordRelationService(
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await new Promise(resolve => setTimeout(resolve, 10))

      let thrown: unknown
      await service
        .resyncConnectorRecords({
          connectorName: 'Slack',
          connectorId: 'conn-2',
          orgId: 'org-1',
          origin: 'slack',
        })
        .catch((e: unknown) => {
          thrown = e
        })

      expect(thrown).to.be.instanceOf(ServiceUnavailableError)
      expect((thrown as ServiceUnavailableError).statusCode).to.equal(503)
      expect((thrown as Error).message).to.equal(RESYNC_NOT_QUEUED_MESSAGE)
    })
  })

  // -----------------------------------------------------------------------
  // createResyncConnectorEventPayload
  // -----------------------------------------------------------------------
  describe('createResyncConnectorEventPayload', () => {
    it('should create proper payload', async () => {
      const service = new RecordRelationService(
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
})

describe('RecordRelationService - additional coverage', () => {
  let mockSyncEventProducer: any
  let mockDefaultConfig: any

  beforeEach(() => {
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

  describe('initializeSyncEventProducer - error path', () => {
    it('should throw InternalServerError when sync event producer start fails', async () => {
      const failingSyncProducer = {
        start: sinon.stub().rejects(new Error('Sync kafka failed')),
        publishEvent: sinon.stub(),
        stop: sinon.stub(),
      }

      try {
        new RecordRelationService(
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

  describe('createUpdateRecordEventPayload - edge cases', () => {
    it('should handle record with no sourceLastModifiedTimestamp', async () => {
      const service = new RecordRelationService(
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

  describe('resyncConnectorRecords - event type construction', () => {
    it('should build event type from connector name', async () => {
      const service = new RecordRelationService(
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

  describe('resync event type derivation', () => {
    /**
     * Node and Python must agree on this string or the event is published to a
     * type nobody consumes. Python uses str.replace, which strips EVERY space;
     * JavaScript's replace with a string pattern strips only the first, so a
     * three-word type produced "confluencedata center.resync" and resync was
     * silently dead for Confluence Data Center and Jira Data Center.
     */
    const publishedTypeFor = async (connectorName: string): Promise<string> => {
      mockSyncEventProducer.publishEvent.resetHistory()
      const service = new RecordRelationService(
        mockSyncEventProducer,
        mockDefaultConfig,
      )
      await service.resyncConnectorRecords({
        connectorName,
        connectorId: 'conn-1',
        orgId: 'org-1',
        origin: 'CONNECTOR',
        fullSync: false,
      })
      return mockSyncEventProducer.publishEvent.firstCall.args[0].eventType
    }

    it('strips every space, not just the first', async () => {
      expect(await publishedTypeFor('Confluence Data Center')).to.equal('confluencedatacenter.resync')
      expect(await publishedTypeFor('Jira Data Center')).to.equal('jiradatacenter.resync')
    })

    it('leaves one- and two-word types unchanged', async () => {
      expect(await publishedTypeFor('Google Drive')).to.equal('googledrive.resync')
      expect(await publishedTypeFor('MinIO')).to.equal('minio.resync')
    })

    it('never yields an event type containing a space', async () => {
      const types = ['Confluence Data Center', 'Jira Data Center', 'Google Drive',
                     'Local FS', 'MinIO', 'Azure Blob Storage']
      for (const t of types) {
        expect(await publishedTypeFor(t)).to.not.contain(' ')
      }
    })
  })
})
