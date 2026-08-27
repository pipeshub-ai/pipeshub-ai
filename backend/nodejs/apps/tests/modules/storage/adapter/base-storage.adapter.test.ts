import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageServiceAdapter } from '../../../../src/modules/storage/adapter/base-storage.adapter'

describe('storage/adapter/base-storage.adapter', () => {
  let adapter: StorageServiceAdapter
  let mockStorageService: any

  beforeEach(() => {
    mockStorageService = {
      uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'url' }),
      updateBuffer: sinon.stub().resolves({ statusCode: 200, data: 'url' }),
      getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('test') }),
      getSignedUrl: sinon.stub().resolves({ statusCode: 200, data: 'signed-url' }),
      getMultipartUploadId: sinon.stub().resolves({ statusCode: 200, data: { uploadId: 'id' } }),
      generatePresignedUrlForPart: sinon.stub().resolves({ statusCode: 200, data: { url: 'part-url', partNumber: 1 } }),
      completeMultipartUpload: sinon.stub().resolves({ statusCode: 200, data: { url: 'final-url' } }),
      generatePresignedUrlForDirectUpload: sinon.stub().resolves({ statusCode: 200, data: { url: 'direct-url' } }),
    }
    adapter = new StorageServiceAdapter(mockStorageService)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('uploadDocumentToStorageService', () => {
    it('should delegate to adapter', async () => {
      const payload: any = { buffer: Buffer.from('test'), mimeType: 'text/plain', documentPath: '/path' }
      const result = await adapter.uploadDocumentToStorageService(payload)
      expect(result.statusCode).to.equal(200)
      expect(mockStorageService.uploadDocumentToStorageService.calledOnce).to.be.true
    })
  })

  describe('updateBuffer', () => {
    it('should delegate to adapter', async () => {
      const result = await adapter.updateBuffer(Buffer.from('test'), {} as any)
      expect(result.statusCode).to.equal(200)
      expect(mockStorageService.updateBuffer.calledOnce).to.be.true
    })
  })

  describe('getBufferFromStorageService', () => {
    it('should delegate to adapter with document and optional version', async () => {
      const result = await adapter.getBufferFromStorageService({} as any, 1)
      expect(result.statusCode).to.equal(200)
      expect(mockStorageService.getBufferFromStorageService.calledOnceWith({}, 1)).to.be.true
    })
  })

  describe('getSignedUrl', () => {
    it('should delegate to adapter with all parameters', async () => {
      const doc: any = {}
      const result = await adapter.getSignedUrl(doc, 1, 'file.pdf', 7200)
      expect(result.statusCode).to.equal(200)
      expect(mockStorageService.getSignedUrl.calledOnceWith(doc, 1, 'file.pdf', 7200)).to.be.true
    })
  })

  describe('getMultipartUploadId', () => {
    it('should delegate to adapter', async () => {
      const result = await adapter.getMultipartUploadId('/path', 'text/plain')
      expect(result.statusCode).to.equal(200)
    })

    it('should reject if method not implemented', async () => {
      const adapterNoMethod = new StorageServiceAdapter({
        ...mockStorageService,
        getMultipartUploadId: undefined,
      })
      try {
        await adapterNoMethod.getMultipartUploadId('/path', 'text/plain')
        expect.fail('Should have rejected')
      } catch (error: any) {
        expect(error.message).to.equal('Method not implemented')
      }
    })
  })

  describe('generatePresignedUrlForPart', () => {
    it('should delegate to adapter', async () => {
      const result = await adapter.generatePresignedUrlForPart('/path', 1, 'upload-id')
      expect(result.statusCode).to.equal(200)
    })
  })

  describe('completeMultipartUpload', () => {
    it('should delegate to adapter', async () => {
      const parts = [{ ETag: 'etag', PartNumber: 1 }]
      const result = await adapter.completeMultipartUpload('/path', 'upload-id', parts)
      expect(result.statusCode).to.equal(200)
    })
  })

  describe('generatePresignedUrlForDirectUpload', () => {
    it('should delegate to adapter', async () => {
      const result = await adapter.generatePresignedUrlForDirectUpload('/path')
      expect(result.statusCode).to.equal(200)
    })

    it('should reject if method not implemented', async () => {
      const adapterNoMethod = new StorageServiceAdapter({
        ...mockStorageService,
        generatePresignedUrlForDirectUpload: undefined,
      })
      try {
        await adapterNoMethod.generatePresignedUrlForDirectUpload('/path')
        expect.fail('Should have rejected')
      } catch (error: any) {
        expect(error.message).to.equal('Method not implemented')
      }
    })
  })

  describe('deleteObject', () => {
    it('should delegate to the underlying adapter', async () => {
      const deleteStub = sinon.stub().resolves({ statusCode: 200, data: undefined })
      const adapterWithDelete = new StorageServiceAdapter({
        ...mockStorageService,
        deleteObject: deleteStub,
      })

      const result = await adapterWithDelete.deleteObject('records/conn-1/file')

      expect(result.statusCode).to.equal(200)
      expect(deleteStub.calledOnceWith('records/conn-1/file')).to.be.true
    })

    it('should reject with error message when deleteObject is not implemented', async () => {
      const adapterNoDelete = new StorageServiceAdapter({
        ...mockStorageService,
        deleteObject: undefined,
      })

      try {
        await adapterNoDelete.deleteObject('records/conn-1/file')
        expect.fail('Should have rejected')
      } catch (error: any) {
        expect(error.message).to.equal('deleteObject not implemented for this storage provider')
      }
    })
  })

  describe('copyObject', () => {
    it('should delegate to the underlying adapter', async () => {
      const copyStub = sinon.stub().resolves({ statusCode: 200, data: 'https://dest.url' })
      const adapterWithCopy = new StorageServiceAdapter({
        ...mockStorageService,
        copyObject: copyStub,
      })

      const result = await adapterWithCopy.copyObject('records/src/file', 'records/dst/file')

      expect(result.statusCode).to.equal(200)
      expect(copyStub.calledOnceWith('records/src/file', 'records/dst/file')).to.be.true
    })

    it('should reject with error message when copyObject is not implemented', async () => {
      const adapterNoCopy = new StorageServiceAdapter({
        ...mockStorageService,
        copyObject: undefined,
      })

      try {
        await adapterNoCopy.copyObject('records/src/file', 'records/dst/file')
        expect.fail('Should have rejected')
      } catch (error: any) {
        expect(error.message).to.equal('copyObject not implemented for this storage provider')
      }
    })
  })

  describe('copyTree', () => {
    it('should delegate to the underlying adapter', async () => {
      const copyTreeStub = sinon.stub().resolves({ statusCode: 200, data: undefined })
      const adapterWithCopyTree = new StorageServiceAdapter({
        ...mockStorageService,
        copyTree: copyTreeStub,
      })

      const result = await adapterWithCopyTree.copyTree('records/src', 'records/dst')

      expect(result.statusCode).to.equal(200)
      expect(copyTreeStub.calledOnceWith('records/src', 'records/dst')).to.be.true
    })

    it('should reject with error message when copyTree is not implemented', async () => {
      const adapterNoCopyTree = new StorageServiceAdapter({
        ...mockStorageService,
        copyTree: undefined,
      })

      try {
        await adapterNoCopyTree.copyTree('records/src', 'records/dst')
        expect.fail('Should have rejected')
      } catch (error: any) {
        expect(error.message).to.equal('copyTree not implemented for this storage provider')
      }
    })
  })

  describe('renameTree', () => {
    it('should delegate to the underlying provider', async () => {
      const inner = { renameTree: sinon.stub().resolves({ statusCode: 200, data: undefined }) }
      const adapter = new StorageServiceAdapter(inner as any)
      const result = await adapter.renameTree('a', 'b')
      expect(result.statusCode).to.equal(200)
      expect(inner.renameTree.calledWith('a', 'b')).to.be.true
    })

    it('rejects when the provider has no native implementation', async () => {
      const adapter = new StorageServiceAdapter({} as any)
      try {
        await adapter.renameTree('a', 'b')
        expect.fail('Should have rejected')
      } catch (error: any) {
        expect(error.message).to.equal('renameTree not implemented for this storage provider')
      }
    })
  })
})
