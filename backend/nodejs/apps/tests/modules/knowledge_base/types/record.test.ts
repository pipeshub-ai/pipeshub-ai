import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('knowledge_base/types/record', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/knowledge_base/types/record')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/knowledge_base/types/record')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('IRecordDocument interface', () => {
    it('should allow creating objects with all required fields', () => {
      const record: import('../../../../src/modules/knowledge_base/types/record').IRecordDocument = {
        _key: 'rec-123',
        orgId: 'org-456',
        recordName: 'test-record',
        externalRecordId: 'ext-123',
        recordType: 'FILE',
        origin: 'UPLOAD',
        createdAtTimestamp: Date.now(),
        connectorId: 'conn-789',
      }
      expect(record._key).to.equal('rec-123')
      expect(record.recordName).to.equal('test-record')
      expect(record.recordType).to.equal('FILE')
      expect(record.origin).to.equal('UPLOAD')
    })

    it('should allow all valid RecordType values', () => {
      const validTypes: import('../../../../src/modules/knowledge_base/types/record').RecordType[] = [
        'FILE', 'WEBPAGE', 'COMMENT', 'MESSAGE', 'EMAIL', 'TICKET', 'OTHERS',
      ]
      validTypes.forEach((type) => {
        expect(type).to.be.a('string')
      })
      expect(validTypes).to.have.lengthOf(7)
    })

    it('should allow all valid OriginType values', () => {
      const validOrigins: import('../../../../src/modules/knowledge_base/types/record').OriginType[] = [
        'UPLOAD', 'CONNECTOR',
      ]
      expect(validOrigins).to.have.lengthOf(2)
    })

    it('should allow all valid ConnectorName values', () => {
      const validConnectors: import('../../../../src/modules/knowledge_base/types/record').ConnectorName[] = [
        'ONEDRIVE', 'GOOGLE_DRIVE', 'CONFLUENCE', 'JIRA', 'SLACK',
        'SHAREPOINT ONLINE', 'GMAIL', 'NOTION',
      ]
      expect(validConnectors).to.have.lengthOf(8)
    })

    it('should allow all valid IndexingStatus values', () => {
      const validStatuses: import('../../../../src/modules/knowledge_base/types/record').IndexingStatus[] = [
        'NOT_STARTED', 'PAUSED', 'IN_PROGRESS', 'COMPLETED', 'FAILED',
        'FILE_TYPE_NOT_SUPPORTED', 'AUTO_INDEX_OFF', 'EMPTY',
        'ENABLE_MULTIMODAL_MODELS', 'QUEUED',
      ]
      expect(validStatuses).to.have.lengthOf(10)
    })

    it('should allow optional flags with defaults description', () => {
      const record: import('../../../../src/modules/knowledge_base/types/record').IRecordDocument = {
        _key: 'rec-1',
        orgId: 'org-1',
        recordName: 'test',
        externalRecordId: 'ext-1',
        recordType: 'WEBPAGE',
        origin: 'CONNECTOR',
        createdAtTimestamp: 1000,
        connectorId: 'conn-1',
        isDeletedAtSource: false,
        isDeleted: false,
        isArchived: false,
        isVLMOcrProcessed: false,
        isLatestVersion: true,
        isDirty: false,
        version: 1,
      }
      expect(record.isDeletedAtSource).to.be.false
      expect(record.isLatestVersion).to.be.true
      expect(record.version).to.equal(1)
    })
  })

  describe('IFileBuffer interface', () => {
    it('should allow creating objects conforming to IFileBuffer shape', () => {
      const fileBuffer: import('../../../../src/modules/knowledge_base/types/record').IFileBuffer = {
        originalname: 'test.pdf',
        mimetype: 'application/pdf',
        size: 2048,
        buffer: Buffer.from('test content'),
      }
      expect(fileBuffer.originalname).to.equal('test.pdf')
      expect(fileBuffer.mimetype).to.equal('application/pdf')
      expect(fileBuffer.size).to.equal(2048)
      expect(fileBuffer.buffer).to.be.instanceOf(Buffer)
    })

    it('should allow optional encoding field', () => {
      const fileBuffer: import('../../../../src/modules/knowledge_base/types/record').IFileBuffer = {
        originalname: 'test.txt',
        mimetype: 'text/plain',
        size: 100,
        buffer: Buffer.from('hello'),
        encoding: 'utf-8',
      }
      expect(fileBuffer.encoding).to.equal('utf-8')
    })
  })
})
