import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('knowledge_base/types/service.records.response', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/knowledge_base/types/service.records.response')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/knowledge_base/types/service.records.response')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('IServiceRecord interface', () => {
    it('should allow creating conforming objects', () => {
      const record: import('../../../../src/modules/knowledge_base/types/service.records.response').IServiceRecord = {
        _key: 'key-1',
        _id: 'records/key-1',
        _rev: 'rev-1',
        orgId: 'org-1',
        recordName: 'test.pdf',
        externalRecordId: 'ext-1',
        recordType: 'FILE',
        origin: 'UPLOAD',
        createdAtTimestamp: '1234567890',
        updatedAtTimestamp: '1234567890',
        isDeleted: false,
        isArchived: false,
        indexingStatus: 'COMPLETED',
        version: 1,
        summaryDocumentId: 'summary-1',
        virtualRecordId: 'vr-1',
        fileRecord: null,
        mailRecord: null,
      }
      expect(record._key).to.equal('key-1')
      expect(record.recordName).to.equal('test.pdf')
      expect(record.fileRecord).to.be.null
    })
  })

  describe('IServiceFileRecord interface', () => {
    it('should allow creating conforming objects', () => {
      const fileRecord: import('../../../../src/modules/knowledge_base/types/service.records.response').IServiceFileRecord = {
        _key: 'fk-1',
        _id: 'files/fk-1',
        _rev: 'rev-1',
        orgId: 'org-1',
        name: 'document.pdf',
        isFile: true,
        extension: 'pdf',
        mimeType: 'application/pdf',
        sizeInBytes: 1024,
        webUrl: 'https://example.com/doc.pdf',
        path: '/docs/document.pdf',
      }
      expect(fileRecord.name).to.equal('document.pdf')
      expect(fileRecord.isFile).to.be.true
    })
  })

  describe('IServiceMailRecord interface', () => {
    it('should allow creating conforming objects', () => {
      const mailRecord: import('../../../../src/modules/knowledge_base/types/service.records.response').IServiceMailRecord = {
        _key: 'mk-1',
        _id: 'mails/mk-1',
        _rev: 'rev-1',
        threadId: 'thread-1',
        isParent: true,
        internalDate: '1234567890',
        subject: 'Test Email',
        date: '2026-01-01',
        from: 'sender@test.com',
        to: 'receiver@test.com',
        cc: ['cc@test.com'],
        bcc: [],
        messageIdHeader: 'msg-id-1',
        historyId: 'hist-1',
        webUrl: 'https://mail.example.com/1',
        labelIds: ['INBOX', 'UNREAD'],
      }
      expect(mailRecord.subject).to.equal('Test Email')
      expect(mailRecord.isParent).to.be.true
      expect(mailRecord.labelIds).to.have.lengthOf(2)
    })
  })

  describe('IServicePermissions interface', () => {
    it('should allow creating conforming objects', () => {
      const permissions: import('../../../../src/modules/knowledge_base/types/service.records.response').IServicePermissions = {
        id: 'perm-1',
        name: 'John Doe',
        type: 'USER',
        relationship: 'OWNER',
      }
      expect(permissions.id).to.equal('perm-1')
      expect(permissions.type).to.equal('USER')
    })
  })

  describe('IServiceRecordsResponse interface', () => {
    it('should allow creating conforming objects', () => {
      const response: import('../../../../src/modules/knowledge_base/types/service.records.response').IServiceRecordsResponse = {
        record: {
          _key: 'key-1',
          _id: 'records/key-1',
          _rev: 'rev-1',
          orgId: 'org-1',
          recordName: 'test.pdf',
          externalRecordId: 'ext-1',
          recordType: 'FILE',
          origin: 'UPLOAD',
          createdAtTimestamp: '123',
          updatedAtTimestamp: '456',
          isDeleted: false,
          isArchived: false,
          indexingStatus: 'COMPLETED',
          version: 1,
          summaryDocumentId: 'sum-1',
          virtualRecordId: 'vr-1',
          fileRecord: null,
          mailRecord: null,
        },
        knowledgeBase: {
          id: 'kb-1',
          name: 'Test KB',
          orgId: 'org-1',
        },
        permissions: [],
      }
      expect(response.record._key).to.equal('key-1')
      expect(response.knowledgeBase.name).to.equal('Test KB')
      expect(response.permissions).to.be.an('array')
    })
  })

  describe('IServiceDeleteRecordResponse interface', () => {
    it('should allow creating conforming objects', () => {
      const response: import('../../../../src/modules/knowledge_base/types/service.records.response').IServiceDeleteRecordResponse = {
        status: 'success',
        message: 'Record deleted successfully',
        response: { deletedCount: 1 },
      }
      expect(response.status).to.equal('success')
      expect(response.message).to.equal('Record deleted successfully')
    })
  })
})
