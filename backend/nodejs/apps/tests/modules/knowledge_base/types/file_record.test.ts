import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('knowledge_base/types/file_record', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/knowledge_base/types/file_record')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/knowledge_base/types/file_record')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  it('should allow creating objects conforming to IFileRecordDocument shape', () => {
    const fileRecord: import('../../../../src/modules/knowledge_base/types/file_record').IFileRecordDocument = {
      orgId: 'org-123',
      name: 'document.pdf',
      isFile: true,
      extension: 'pdf',
      mimeType: 'application/pdf',
      sizeInBytes: 1024,
      webUrl: 'https://example.com/doc.pdf',
    }
    expect(fileRecord.orgId).to.equal('org-123')
    expect(fileRecord.name).to.equal('document.pdf')
    expect(fileRecord.isFile).to.be.true
    expect(fileRecord.extension).to.equal('pdf')
  })

  it('should allow minimal required fields only', () => {
    const fileRecord: import('../../../../src/modules/knowledge_base/types/file_record').IFileRecordDocument = {
      orgId: 'org-456',
      name: 'test.txt',
    }
    expect(fileRecord.orgId).to.equal('org-456')
    expect(fileRecord.name).to.equal('test.txt')
    expect(fileRecord.isFile).to.be.undefined
  })

  it('should allow hash fields', () => {
    const fileRecord: import('../../../../src/modules/knowledge_base/types/file_record').IFileRecordDocument = {
      orgId: 'org-789',
      name: 'test.txt',
      quickXorHash: 'hash1',
      crc32Hash: 'hash2',
      sha1Hash: 'hash3',
      sha256Hash: 'hash4',
    }
    expect(fileRecord.quickXorHash).to.equal('hash1')
    expect(fileRecord.sha256Hash).to.equal('hash4')
  })

  it('should allow _key and other optional fields', () => {
    const fileRecord: import('../../../../src/modules/knowledge_base/types/file_record').IFileRecordDocument = {
      _key: 'key-123',
      orgId: 'org-123',
      name: 'file.doc',
      etag: 'etag-val',
      ctag: 'ctag-val',
      externalFileId: 'ext-id-1',
      path: '/docs/file.doc',
    }
    expect(fileRecord._key).to.equal('key-123')
    expect(fileRecord.path).to.equal('/docs/file.doc')
  })
})
