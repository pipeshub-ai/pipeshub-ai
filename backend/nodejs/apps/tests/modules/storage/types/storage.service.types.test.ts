import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'

describe('storage/types/storage.service.types', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('StorageVendor enum', () => {
    it('should have S3', () => {
      expect(StorageVendor.S3).to.equal('s3')
    })

    it('should have AzureBlob', () => {
      expect(StorageVendor.AzureBlob).to.equal('azureBlob')
    })

    it('should have Local', () => {
      expect(StorageVendor.Local).to.equal('local')
    })

    it('should have exactly 3 vendors', () => {
      const values = Object.values(StorageVendor).filter(
        (v) => typeof v === 'string',
      )
      expect(values).to.have.lengthOf(3)
    })
  })

  describe('StorageServiceResponse interface', () => {
    it('should allow creating conforming objects with data', () => {
      const response: import('../../../../src/modules/storage/types/storage.service.types').StorageServiceResponse<string> = {
        statusCode: 200,
        data: 'https://example.com/doc',
        msg: 'Success',
      }
      expect(response.statusCode).to.equal(200)
      expect(response.data).to.equal('https://example.com/doc')
    })

    it('should allow error responses without data', () => {
      const response: import('../../../../src/modules/storage/types/storage.service.types').StorageServiceResponse<string> = {
        statusCode: 404,
        msg: 'Document not found',
      }
      expect(response.statusCode).to.equal(404)
      expect(response.data).to.be.undefined
    })

    it('should work with Buffer type parameter', () => {
      const response: import('../../../../src/modules/storage/types/storage.service.types').StorageServiceResponse<Buffer> = {
        statusCode: 200,
        data: Buffer.from('content'),
      }
      expect(response.data).to.be.instanceOf(Buffer)
    })
  })

  describe('Document interface', () => {
    it('should allow creating conforming objects with required fields', () => {
      const doc: import('../../../../src/modules/storage/types/storage.service.types').Document = {
        documentName: 'test.pdf',
        isVersionedFile: true,
        orgId: {} as any,
        initiatorUserId: {} as any,
        extension: 'pdf',
        currentVersion: 1,
        isDeleted: false,
        storageVendor: StorageVendor.S3,
      }
      expect(doc.documentName).to.equal('test.pdf')
      expect(doc.isVersionedFile).to.be.true
      expect(doc.storageVendor).to.equal('s3')
    })

    it('should allow optional fields', () => {
      const doc: import('../../../../src/modules/storage/types/storage.service.types').Document = {
        documentName: 'test.pdf',
        isVersionedFile: false,
        orgId: {} as any,
        initiatorUserId: null,
        extension: 'pdf',
        currentVersion: 0,
        isDeleted: false,
        storageVendor: StorageVendor.Local,
        documentPath: '/docs/test.pdf',
        mimeType: 'application/pdf',
        sizeInBytes: 1024,
        tags: ['important', 'report'],
      }
      expect(doc.initiatorUserId).to.be.null
      expect(doc.tags).to.have.lengthOf(2)
    })
  })

  describe('DocumentPermission type', () => {
    it('should allow valid permission values', () => {
      const permissions: import('../../../../src/modules/storage/types/storage.service.types').DocumentPermission[] = [
        'owner', 'editor', 'commentator', 'readonly',
      ]
      expect(permissions).to.have.lengthOf(4)
      expect(permissions).to.include('owner')
      expect(permissions).to.include('readonly')
    })
  })

  describe('FilePayload interface', () => {
    it('should allow creating conforming objects', () => {
      const payload: import('../../../../src/modules/storage/types/storage.service.types').FilePayload = {
        documentPath: '/uploads/test.pdf',
        buffer: Buffer.from('file content'),
        mimeType: 'application/pdf',
        isVersioned: true,
      }
      expect(payload.documentPath).to.equal('/uploads/test.pdf')
      expect(payload.buffer).to.be.instanceOf(Buffer)
      expect(payload.mimeType).to.equal('application/pdf')
      expect(payload.isVersioned).to.be.true
    })
  })

  describe('StorageInfo interface', () => {
    it('should allow creating conforming objects', () => {
      const info: import('../../../../src/modules/storage/types/storage.service.types').StorageInfo = {
        url: 'https://s3.example.com/doc.pdf',
        localPath: '/local/docs/doc.pdf',
      }
      expect(info.url).to.equal('https://s3.example.com/doc.pdf')
    })
  })

  describe('DocumentVersion interface', () => {
    it('should allow creating conforming objects', () => {
      const version: import('../../../../src/modules/storage/types/storage.service.types').DocumentVersion = {
        version: 2,
        note: 'Updated formatting',
        extension: 'pdf',
        currentVersion: 2,
        createdAt: Date.now(),
        size: 2048,
      }
      expect(version.version).to.equal(2)
      expect(version.note).to.equal('Updated formatting')
    })
  })

  describe('CustomMetadata interface', () => {
    it('should allow creating conforming objects', () => {
      const meta: import('../../../../src/modules/storage/types/storage.service.types').CustomMetadata = {
        key: 'department',
        value: 'engineering',
      }
      expect(meta.key).to.equal('department')
      expect(meta.value).to.equal('engineering')
    })
  })
})
