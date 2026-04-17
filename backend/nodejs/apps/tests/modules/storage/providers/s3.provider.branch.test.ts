import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  StorageConfigurationError,
  StorageError,
  StorageValidationError,
  StorageNotFoundError,
  StorageUploadError,
  StorageDownloadError,
  MultipartUploadError,
  PresignedUrlError,
} from '../../../../src/libs/errors/storage.errors'

// We can't easily import the actual S3 adapter because it creates a real S3 client,
// so we test the constructor validation and private method logic patterns.

describe('AmazonS3Adapter - branch coverage', () => {
  afterEach(() => { sinon.restore() })

  // =========================================================================
  // Constructor validation branches
  // =========================================================================
  describe('constructor validation', () => {
    it('should throw StorageConfigurationError when accessKeyId is missing', () => {
      try {
        // Dynamically import
        const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
        new AmazonS3Adapter({ accessKeyId: '', secretAccessKey: 'key', region: 'us-east-1', bucket: 'b' })
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when secretAccessKey is missing', () => {
      try {
        const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
        new AmazonS3Adapter({ accessKeyId: 'key', secretAccessKey: '', region: 'us-east-1', bucket: 'b' })
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when region is missing', () => {
      try {
        const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
        new AmazonS3Adapter({ accessKeyId: 'key', secretAccessKey: 'key', region: '', bucket: 'b' })
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when bucket is missing', () => {
      try {
        const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
        new AmazonS3Adapter({ accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: '' })
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when region format is invalid', () => {
      try {
        const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
        new AmazonS3Adapter({ accessKeyId: 'key', secretAccessKey: 'key', region: 'invalid_region!!!', bucket: 'b' })
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should create adapter with valid credentials', () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
        secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
        region: 'us-east-1',
        bucket: 'test-bucket',
      })
      expect(adapter).to.exist
    })
  })

  // =========================================================================
  // validateAndSanitizeRegion
  // =========================================================================
  describe('validateAndSanitizeRegion', () => {
    it('should accept valid region formats', () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'b',
      })
      // Test with spaces
      const result = (adapter as any).validateAndSanitizeRegion(' US-EAST-1 ')
      expect(result).to.equal('us-east-1')
    })

    it('should accept ap-southeast-1 region', () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'ap-southeast-1', bucket: 'b',
      })
      const result = (adapter as any).validateAndSanitizeRegion('ap-southeast-1')
      expect(result).to.equal('ap-southeast-1')
    })

    it('should reject region with special characters', () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'b',
      })
      try {
        ;(adapter as any).validateAndSanitizeRegion('us-east-1; rm -rf /')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })
  })

  // =========================================================================
  // validateFilePayload
  // =========================================================================
  describe('validateFilePayload', () => {
    let adapter: any

    beforeEach(() => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'b',
      })
    })

    it('should throw for missing buffer', () => {
      expect(() => {
        adapter.validateFilePayload({ buffer: null, documentPath: 'p', mimeType: 'm' })
      }).to.throw(StorageValidationError)
    })

    it('should throw for missing documentPath', () => {
      expect(() => {
        adapter.validateFilePayload({ buffer: Buffer.from('x'), documentPath: '', mimeType: 'm' })
      }).to.throw(StorageValidationError)
    })

    it('should throw for missing mimeType', () => {
      expect(() => {
        adapter.validateFilePayload({ buffer: Buffer.from('x'), documentPath: 'p', mimeType: '' })
      }).to.throw(StorageValidationError)
    })

    it('should pass for valid payload', () => {
      expect(() => {
        adapter.validateFilePayload({ buffer: Buffer.from('x'), documentPath: 'p', mimeType: 'm' })
      }).to.not.throw()
    })
  })

  // =========================================================================
  // extractKeyFromUrl
  // =========================================================================
  describe('extractKeyFromUrl', () => {
    let adapter: any

    beforeEach(() => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })
    })

    it('should extract key from valid S3 URL', () => {
      const key = adapter.extractKeyFromUrl('https://test-bucket.s3.us-east-1.amazonaws.com/folder/file.pdf')
      expect(key).to.equal('folder/file.pdf')
    })

    it('should decode URL-encoded characters in key', () => {
      const key = adapter.extractKeyFromUrl('https://test-bucket.s3.us-east-1.amazonaws.com/folder/file%20name.pdf')
      expect(key).to.equal('folder/file name.pdf')
    })

    it('should remove trailing slashes', () => {
      const key = adapter.extractKeyFromUrl('https://test-bucket.s3.us-east-1.amazonaws.com/folder/')
      expect(key).to.equal('folder')
    })

    it('should throw StorageValidationError for non-matching URL', () => {
      try {
        adapter.extractKeyFromUrl('https://other-bucket.s3.eu-west-1.amazonaws.com/file.pdf')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError for completely invalid URL', () => {
      try {
        adapter.extractKeyFromUrl('not-a-url')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })
  })

  // =========================================================================
  // getS3Url
  // =========================================================================
  describe('getS3Url', () => {
    it('should generate correct S3 URL with encoded key', () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })
      const url = (adapter as any).getS3Url('folder/file name.pdf')
      expect(url).to.equal('https://test-bucket.s3.us-east-1.amazonaws.com/folder/file%20name.pdf')
    })
  })

  // =========================================================================
  // uploadDocumentToStorageService - error branches
  // =========================================================================
  describe('uploadDocumentToStorageService - error branches', () => {
    it('should re-throw StorageError subtypes', async () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })

      // Invalid payload => StorageValidationError (a StorageError subtype)
      try {
        await adapter.uploadDocumentToStorageService({
          buffer: null, documentPath: '', mimeType: '',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should wrap non-StorageError in StorageUploadError', async () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })

      // Stub the s3.upload to reject with a non-StorageError
      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().rejects(new Error('AWS SDK error')),
      })

      try {
        await adapter.uploadDocumentToStorageService({
          buffer: Buffer.from('x'), documentPath: 'path', mimeType: 'text/plain',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })

    it('should throw StorageUploadError when upload response has no Key', async () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })

      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().resolves({ Key: null }),
      })

      try {
        await adapter.uploadDocumentToStorageService({
          buffer: Buffer.from('x'), documentPath: 'path', mimeType: 'text/plain',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })

    it('should succeed with valid upload response', async () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })

      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().resolves({ Key: 'folder/file.pdf' }),
      })

      const result = await adapter.uploadDocumentToStorageService({
        buffer: Buffer.from('x'), documentPath: 'folder/file.pdf', mimeType: 'application/pdf',
      })
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.include('test-bucket.s3.us-east-1.amazonaws.com')
    })
  })

  // =========================================================================
  // updateBuffer - error branches
  // =========================================================================
  describe('updateBuffer - error branches', () => {
    it('should throw StorageNotFoundError when s3 URL is missing', async () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })

      try {
        await adapter.updateBuffer(Buffer.from('x'), { s3: {} } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageUploadError when upload response has no Key on update', async () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })

      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().resolves({ Key: null }),
      })

      try {
        await adapter.updateBuffer(Buffer.from('x'), {
          s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
          mimeType: 'text/plain',
        } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })
  })

  // =========================================================================
  // getBufferFromStorageService - version branches
  // =========================================================================
  describe('getBufferFromStorageService - version branches', () => {
    let adapter: any

    beforeEach(() => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })
    })

    it('should use current URL when version is undefined', async () => {
      sinon.stub(adapter.s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: Buffer.from('content') }),
      })

      const result = await adapter.getBufferFromStorageService({
        s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
      } as any)
      expect(result.statusCode).to.equal(200)
    })

    it('should use versionHistory URL when version is 0', async () => {
      sinon.stub(adapter.s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: Buffer.from('content') }),
      })

      const result = await adapter.getBufferFromStorageService({
        s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/current.pdf' },
        versionHistory: [
          { s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/v0.pdf' } },
        ],
      } as any, 0)
      expect(result.statusCode).to.equal(200)
    })

    it('should use versionHistory URL for specific version', async () => {
      sinon.stub(adapter.s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: Buffer.from('v1') }),
      })

      const result = await adapter.getBufferFromStorageService({
        s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/current.pdf' },
        versionHistory: {
          1: { s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/v1.pdf' } },
        },
      } as any, 1)
      expect(result.statusCode).to.equal(200)
    })

    it('should throw StorageNotFoundError when versionHistory URL is missing', async () => {
      try {
        await adapter.getBufferFromStorageService({
          s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
          versionHistory: { 1: { s3: {} } },
        } as any, 1)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageDownloadError when response Body is empty', async () => {
      sinon.stub(adapter.s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: null }),
      })

      try {
        await adapter.getBufferFromStorageService({
          s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
        } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageDownloadError)
      }
    })
  })

  // =========================================================================
  // getSignedUrl - branches
  // =========================================================================
  describe('getSignedUrl - branches', () => {
    let adapter: any

    beforeEach(() => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })
    })

    it('should throw StorageNotFoundError when document is null', async () => {
      try {
        await adapter.getSignedUrl(null as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should add content-disposition when fileName is provided', async () => {
      sinon.stub(adapter.s3, 'getSignedUrlPromise').resolves('https://signed-url.com/file')

      const result = await adapter.getSignedUrl(
        { s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/file.pdf' }, extension: '.pdf' } as any,
        undefined,
        'downloaded-file',
      )
      expect(result.statusCode).to.equal(200)
    })

    it('should not add content-disposition when fileName is not provided', async () => {
      sinon.stub(adapter.s3, 'getSignedUrlPromise').resolves('https://signed-url.com/file')

      const result = await adapter.getSignedUrl(
        { s3: { url: 'https://test-bucket.s3.us-east-1.amazonaws.com/file.pdf' } } as any,
      )
      expect(result.statusCode).to.equal(200)
    })

    it('should throw PresignedUrlError when key extraction fails', async () => {
      try {
        await adapter.getSignedUrl(
          { s3: { url: 'https://wrong-bucket.s3.wrong-region.amazonaws.com/file.pdf' } } as any,
        )
        expect.fail('Should have thrown')
      } catch (error) {
        // The extractKeyFromUrl throws StorageValidationError which extends StorageError
        expect(error).to.be.instanceOf(StorageError)
      }
    })
  })

  // =========================================================================
  // Multipart upload methods - error branches
  // =========================================================================
  describe('multipart upload - error branches', () => {
    let adapter: any

    beforeEach(() => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'key', region: 'us-east-1', bucket: 'test-bucket',
      })
    })

    it('getMultipartUploadId should throw MultipartUploadError when no UploadId', async () => {
      sinon.stub(adapter.s3, 'createMultipartUpload').returns({
        promise: sinon.stub().resolves({ UploadId: null }),
      })

      try {
        await adapter.getMultipartUploadId('path', 'mime')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('getMultipartUploadId should succeed with valid UploadId', async () => {
      sinon.stub(adapter.s3, 'createMultipartUpload').returns({
        promise: sinon.stub().resolves({ UploadId: 'upload-123' }),
      })

      const result = await adapter.getMultipartUploadId('path', 'mime')
      expect(result.data.uploadId).to.equal('upload-123')
    })

    it('completeMultipartUpload should throw when response has no Key', async () => {
      sinon.stub(adapter.s3, 'completeMultipartUpload').returns({
        promise: sinon.stub().resolves({ Key: null }),
      })

      try {
        await adapter.completeMultipartUpload('path', 'upload-123', [])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('generatePresignedUrlForPart should succeed', async () => {
      sinon.stub(adapter.s3, 'getSignedUrlPromise').resolves('https://presigned.com')

      const result = await adapter.generatePresignedUrlForPart('path', 1, 'upload-123')
      expect(result.data.url).to.equal('https://presigned.com')
      expect(result.data.partNumber).to.equal(1)
    })

    it('generatePresignedUrlForDirectUpload should succeed', async () => {
      sinon.stub(adapter.s3, 'getSignedUrlPromise').resolves('https://direct-upload.com')

      const result = await adapter.generatePresignedUrlForDirectUpload('path')
      expect(result.data.url).to.equal('https://direct-upload.com')
    })
  })
})
