import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('storage/services/storage.service (StorageServiceInterface)', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/storage/services/storage.service')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/storage/services/storage.service')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('StorageServiceInterface', () => {
    it('should allow implementing the interface with required methods', () => {
      const mockService: import('../../../../src/modules/storage/services/storage.service').StorageServiceInterface = {
        uploadDocumentToStorageService: async () => ({
          statusCode: 200,
          data: 'uploaded-url',
        }),
        updateBuffer: async () => ({
          statusCode: 200,
          data: 'updated-url',
        }),
        getBufferFromStorageService: async () => ({
          statusCode: 200,
          data: Buffer.from('content'),
        }),
        getSignedUrl: async () => ({
          statusCode: 200,
          data: 'https://signed-url.com',
        }),
      }
      expect(mockService.uploadDocumentToStorageService).to.be.a('function')
      expect(mockService.updateBuffer).to.be.a('function')
      expect(mockService.getBufferFromStorageService).to.be.a('function')
      expect(mockService.getSignedUrl).to.be.a('function')
    })

    it('should allow optional multipart methods', () => {
      const mockService: import('../../../../src/modules/storage/services/storage.service').StorageServiceInterface = {
        uploadDocumentToStorageService: async () => ({ statusCode: 200, data: 'ok' }),
        updateBuffer: async () => ({ statusCode: 200, data: 'ok' }),
        getBufferFromStorageService: async () => ({ statusCode: 200, data: Buffer.from('') }),
        getSignedUrl: async () => ({ statusCode: 200, data: 'url' }),
        getMultipartUploadId: async () => ({
          statusCode: 200,
          data: { uploadId: 'upload-123' },
        }),
        generatePresignedUrlForPart: async () => ({
          statusCode: 200,
          data: { url: 'https://part-url.com', partNumber: 1 },
        }),
        completeMultipartUpload: async () => ({
          statusCode: 200,
          data: { url: 'https://complete-url.com' },
        }),
        generatePresignedUrlForDirectUpload: async () => ({
          statusCode: 200,
          data: { url: 'https://direct-upload-url.com' },
        }),
      }
      expect(mockService.getMultipartUploadId).to.be.a('function')
      expect(mockService.generatePresignedUrlForPart).to.be.a('function')
      expect(mockService.completeMultipartUpload).to.be.a('function')
      expect(mockService.generatePresignedUrlForDirectUpload).to.be.a('function')
    })
  })
})
