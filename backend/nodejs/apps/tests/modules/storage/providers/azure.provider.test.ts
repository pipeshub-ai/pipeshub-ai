import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  StorageConfigurationError,
  StorageValidationError,
  StorageNotFoundError,
  StorageUploadError,
  StorageDownloadError,
  MultipartUploadError,
  PresignedUrlError,
} from '../../../../src/libs/errors/storage.errors'

describe('AzureBlobStorageAdapter', () => {
  afterEach(() => { sinon.restore() })

  // -------------------------------------------------------------------------
  // constructor
  // -------------------------------------------------------------------------
  describe('constructor', () => {
    it('should throw StorageConfigurationError when accountName is missing', () => {
      try {
        const AzureBlobStorageAdapter = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default
        new AzureBlobStorageAdapter({
          accountName: '', accountKey: 'key', containerName: 'container',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when accountKey is missing', () => {
      try {
        const AzureBlobStorageAdapter = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default
        new AzureBlobStorageAdapter({
          accountName: 'account', accountKey: '', containerName: 'container',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when containerName missing with connection string', () => {
      try {
        const AzureBlobStorageAdapter = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default
        new AzureBlobStorageAdapter({
          azureBlobConnectionString: 'DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net',
          containerName: '',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when all credentials missing', () => {
      try {
        const AzureBlobStorageAdapter = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default
        new AzureBlobStorageAdapter({})
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // multipart upload methods (not implemented)
  // -------------------------------------------------------------------------
  describe('multipart upload methods', () => {
    it('getMultipartUploadId should throw MultipartUploadError', async () => {
      try {
        const proto = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default.prototype
        await proto.getMultipartUploadId('path', 'mime')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('generatePresignedUrlForPart should throw MultipartUploadError', async () => {
      try {
        const proto = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default.prototype
        await proto.generatePresignedUrlForPart('path', 1, 'upload-id')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('completeMultipartUpload should throw MultipartUploadError', async () => {
      try {
        const proto = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default.prototype
        await proto.completeMultipartUpload('path', 'uid', [])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // validateFilePayload (private)
  // -------------------------------------------------------------------------
  describe('validateFilePayload (private)', () => {
    it('should throw StorageValidationError for missing buffer', () => {
      try {
        const proto = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default.prototype
        proto.validateFilePayload({ buffer: null, documentPath: 'path', mimeType: 'text/plain' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError for missing documentPath', () => {
      try {
        const proto = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default.prototype
        proto.validateFilePayload({ buffer: Buffer.from('test'), documentPath: '', mimeType: 'text/plain' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError for missing mimeType', () => {
      try {
        const proto = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default.prototype
        proto.validateFilePayload({ buffer: Buffer.from('test'), documentPath: 'path', mimeType: '' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should not throw for valid payload', () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype
      expect(() => {
        proto.validateFilePayload({
          buffer: Buffer.from('test'), documentPath: 'path', mimeType: 'text/plain',
        })
      }).to.not.throw()
    })
  })

  // -------------------------------------------------------------------------
  // getBlobPath (private)
  // -------------------------------------------------------------------------
  describe('getBlobPath (private)', () => {
    it('should throw StorageValidationError for invalid URL', () => {
      try {
        const proto = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default.prototype
        proto.containerName = 'test-container'
        proto.getBlobPath('not-a-url')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should extract blob path from valid URL', () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype
      proto.containerName = 'mycontainer'
      const path = proto.getBlobPath('https://account.blob.core.windows.net/mycontainer/folder/file.pdf')
      expect(path).to.equal('folder/file.pdf')
    })

    it('should handle deeply nested paths', () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype
      proto.containerName = 'mycontainer'
      const path = proto.getBlobPath('https://account.blob.core.windows.net/mycontainer/a/b/c/file.pdf')
      expect(path).to.equal('a/b/c/file.pdf')
    })
  })

  // -------------------------------------------------------------------------
  // streamToBuffer (private)
  // -------------------------------------------------------------------------
  describe('streamToBuffer (private)', () => {
    it('should convert readable stream to buffer', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      const { Readable } = require('stream')
      const readable = new Readable()
      readable.push(Buffer.from('hello'))
      readable.push(Buffer.from(' world'))
      readable.push(null)

      const result = await proto.streamToBuffer(readable)
      expect(result.toString()).to.equal('hello world')
    })

    it('should handle empty stream', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      const { Readable } = require('stream')
      const readable = new Readable()
      readable.push(null)

      const result = await proto.streamToBuffer(readable)
      expect(result.length).to.equal(0)
    })

    it('should reject on stream error', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      const { Readable } = require('stream')
      const readable = new Readable({
        read() {
          this.destroy(new Error('stream error'))
        },
      })

      try {
        await proto.streamToBuffer(readable)
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('stream error')
      }
    })
  })

  // -------------------------------------------------------------------------
  // uploadDocumentToStorageService
  // -------------------------------------------------------------------------
  describe('uploadDocumentToStorageService', () => {
    it('should throw StorageValidationError for invalid payload', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      try {
        await proto.uploadDocumentToStorageService({
          buffer: null, documentPath: '', mimeType: '',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // updateBuffer
  // -------------------------------------------------------------------------
  describe('updateBuffer', () => {
    it('should throw StorageNotFoundError when azure URL is missing', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      try {
        await proto.updateBuffer(Buffer.from('test'), { azureBlob: undefined })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getBufferFromStorageService
  // -------------------------------------------------------------------------
  describe('getBufferFromStorageService', () => {
    it('should throw StorageNotFoundError when azure URL is missing', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      try {
        await proto.getBufferFromStorageService({ azureBlob: undefined })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageNotFoundError for version without URL', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      try {
        await proto.getBufferFromStorageService({
          azureBlob: { url: 'https://account.blob.core.windows.net/cont/file.pdf' },
          versionHistory: [{}],
        }, 1)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getSignedUrl
  // -------------------------------------------------------------------------
  describe('getSignedUrl', () => {
    it('should throw StorageNotFoundError when azure URL not found for version', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      try {
        await proto.getSignedUrl({ azureBlob: undefined })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageNotFoundError for versioned request with no URL', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      try {
        await proto.getSignedUrl({
          azureBlob: { url: 'https://a.blob.core.windows.net/c/f.pdf' },
          versionHistory: [{}],
        }, 1)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // uploadDocumentToStorageService (with mocked container)
  // -------------------------------------------------------------------------
  describe('uploadDocumentToStorageService (with mock)', () => {
    it('should upload and return blob URL on success', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      const mockBlobClient = {
        uploadData: sinon.stub().resolves({}),
        url: 'https://account.blob.core.windows.net/container/path/file.pdf',
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      const result = await proto.uploadDocumentToStorageService({
        buffer: Buffer.from('test'), documentPath: 'path/file.pdf', mimeType: 'application/pdf',
      })

      expect(result.statusCode).to.equal(200)
      expect(result.data).to.include('blob.core.windows.net')
    })

    it('should throw StorageUploadError on unknown upload error', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      const mockBlobClient = {
        uploadData: sinon.stub().rejects(new Error('network error')),
        url: 'https://account.blob.core.windows.net/container/path/file.pdf',
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      try {
        await proto.uploadDocumentToStorageService({
          buffer: Buffer.from('test'), documentPath: 'path/file.pdf', mimeType: 'application/pdf',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // updateBuffer (with mocked container)
  // -------------------------------------------------------------------------
  describe('updateBuffer (with mock)', () => {
    it('should update and return blob URL on success', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const mockBlobClient = {
        uploadData: sinon.stub().resolves({}),
        url: 'https://account.blob.core.windows.net/testcontainer/path/file.pdf',
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      const result = await proto.updateBuffer(
        Buffer.from('new content'),
        {
          azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/path/file.pdf' },
          mimeType: 'application/pdf',
        },
      )

      expect(result.statusCode).to.equal(200)
    })

    it('should throw StorageUploadError on unknown update error', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const mockBlobClient = {
        uploadData: sinon.stub().rejects(new Error('disk full')),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      try {
        await proto.updateBuffer(Buffer.from('test'), {
          azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/path/file.pdf' },
          mimeType: 'application/pdf',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getBufferFromStorageService (with mocked container)
  // -------------------------------------------------------------------------
  describe('getBufferFromStorageService (with mock)', () => {
    it('should download and return buffer on success', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const { Readable } = require('stream')
      const readable = new Readable()
      readable.push(Buffer.from('file content'))
      readable.push(null)

      const mockBlobClient = {
        download: sinon.stub().resolves({ readableStreamBody: readable }),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      const result = await proto.getBufferFromStorageService({
        azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/path/file.pdf' },
      })

      expect(result.statusCode).to.equal(200)
      expect(result.data.toString()).to.equal('file content')
    })

    it('should throw StorageDownloadError when no readable stream body', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const mockBlobClient = {
        download: sinon.stub().resolves({ readableStreamBody: null }),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      try {
        await proto.getBufferFromStorageService({
          azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/path/file.pdf' },
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageDownloadError)
      }
    })

    it('should use version URL when version is provided', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const { Readable } = require('stream')
      const readable = new Readable()
      readable.push(Buffer.from('version content'))
      readable.push(null)

      const mockBlobClient = {
        download: sinon.stub().resolves({ readableStreamBody: readable }),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      const result = await proto.getBufferFromStorageService({
        azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/current.pdf' },
        versionHistory: [
          { azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/v0.pdf' } },
          { azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/v1.pdf' } },
        ],
      }, 1)

      expect(result.statusCode).to.equal(200)
    })

    it('should use versionHistory URL when version is 0', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const { Readable } = require('stream')
      const readable = new Readable()
      readable.push(Buffer.from('v0 content'))
      readable.push(null)

      const mockBlobClient = {
        download: sinon.stub().resolves({ readableStreamBody: readable }),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      const result = await proto.getBufferFromStorageService({
        azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/current.pdf' },
        versionHistory: [
          { azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/v0.pdf' } },
        ],
      }, 0)

      expect(result.statusCode).to.equal(200)
    })
  })

  // -------------------------------------------------------------------------
  // getSignedUrl (with mocked container)
  // -------------------------------------------------------------------------
  describe('getSignedUrl (with mock)', () => {
    it('should generate signed URL on success', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const mockBlobClient = {
        generateSasUrl: sinon.stub().resolves('https://signed-url.blob.core.windows.net/sas?token=abc'),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      const result = await proto.getSignedUrl({
        azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/file.pdf' },
        extension: '.pdf',
      })

      expect(result.statusCode).to.equal(200)
      expect(result.data).to.include('signed-url')
    })

    it('should include content-disposition when fileName is provided', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const mockBlobClient = {
        generateSasUrl: sinon.stub().resolves('https://signed.url'),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      await proto.getSignedUrl({
        azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/file.pdf' },
        extension: '.pdf',
      }, undefined, 'myfile')

      const sasOpts = mockBlobClient.generateSasUrl.firstCall.args[0]
      expect(sasOpts.contentDisposition).to.include('myfile.pdf')
    })

    it('should throw PresignedUrlError on unknown error', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      const mockBlobClient = {
        generateSasUrl: sinon.stub().rejects(new Error('SAS generation failed')),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      try {
        await proto.getSignedUrl({
          azureBlob: { url: 'https://account.blob.core.windows.net/testcontainer/file.pdf' },
          extension: '.pdf',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // generatePresignedUrlForDirectUpload (with mocked container)
  // -------------------------------------------------------------------------
  describe('generatePresignedUrlForDirectUpload (with mock)', () => {
    it('should generate presigned URL for direct upload', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      const mockBlobClient = {
        generateSasUrl: sinon.stub().resolves('https://direct-upload.blob.core.windows.net/sas'),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      const result = await proto.generatePresignedUrlForDirectUpload('upload/path/file.pdf')

      expect(result.statusCode).to.equal(200)
      expect(result.data.url).to.include('direct-upload')
    })

    it('should throw PresignedUrlError on failure', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      const mockBlobClient = {
        generateSasUrl: sinon.stub().rejects(new Error('SAS error')),
      }
      proto.containerClient = {
        getBlockBlobClient: sinon.stub().returns(mockBlobClient),
      }

      try {
        await proto.generatePresignedUrlForDirectUpload('path/file.pdf')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // ensureContainerExists (private)
  // -------------------------------------------------------------------------
  describe('ensureContainerExists (private)', () => {
    it('should handle container already exists', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      proto.containerClient = {
        createIfNotExists: sinon.stub().resolves({ succeeded: false }),
      }
      proto.logger = { info: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }

      // Should not throw
      await proto.ensureContainerExists()
    })

    it('should handle container creation success', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      proto.containerClient = {
        createIfNotExists: sinon.stub().resolves({ succeeded: true }),
      }
      proto.logger = { info: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }

      // Should not throw
      await proto.ensureContainerExists()
    })

    it('should throw StorageConfigurationError on failure', async () => {
      const proto = require(
        '../../../../src/modules/storage/providers/azure.provider',
      ).default.prototype

      proto.containerName = 'testcontainer'
      proto.containerClient = {
        createIfNotExists: sinon.stub().rejects(new Error('access denied')),
      }

      try {
        await proto.ensureContainerExists()
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })
  })
})
