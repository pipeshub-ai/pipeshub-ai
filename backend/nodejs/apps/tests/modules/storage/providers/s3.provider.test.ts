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

import AmazonS3Adapter from '../../../../src/modules/storage/providers/s3.provider'

// Helper to create a valid adapter instance for method testing
function createAdapter(): AmazonS3Adapter {
  return new AmazonS3Adapter({
    accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
    secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    region: 'us-east-1',
    bucket: 'my-bucket',
  })
}

describe('AmazonS3Adapter', () => {
  afterEach(() => { sinon.restore() })

  // -------------------------------------------------------------------------
  // constructor
  // -------------------------------------------------------------------------
  describe('constructor', () => {
    it('should throw StorageConfigurationError when credentials are missing', () => {
      try {
        new AmazonS3Adapter({ accessKeyId: '', secretAccessKey: '', region: '', bucket: '' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError for invalid region format', () => {
      try {
        new AmazonS3Adapter({
          accessKeyId: 'key', secretAccessKey: 'secret',
          region: 'invalid!region', bucket: 'my-bucket',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError for single-segment region', () => {
      try {
        new AmazonS3Adapter({
          accessKeyId: 'key', secretAccessKey: 'secret',
          region: 'useast', bucket: 'my-bucket',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when only accessKeyId is missing', () => {
      try {
        new AmazonS3Adapter({
          accessKeyId: '', secretAccessKey: 'secret',
          region: 'us-east-1', bucket: 'my-bucket',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when only bucket is missing', () => {
      try {
        new AmazonS3Adapter({
          accessKeyId: 'key', secretAccessKey: 'secret',
          region: 'us-east-1', bucket: '',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should create adapter with valid credentials', () => {
      const adapter = createAdapter()
      expect(adapter).to.be.instanceOf(AmazonS3Adapter)
    })

    it('should accept various valid region formats', () => {
      const validRegions = ['us-east-1', 'eu-west-2', 'ap-southeast-1', 'cn-north-1', 'me-south-1']
      for (const region of validRegions) {
        const adapter = new AmazonS3Adapter({
          accessKeyId: 'key', secretAccessKey: 'secret', region, bucket: 'my-bucket',
        })
        expect(adapter).to.be.instanceOf(AmazonS3Adapter)
      }
    })

    it('should trim and lowercase the region', () => {
      const adapter = new AmazonS3Adapter({
        accessKeyId: 'key', secretAccessKey: 'secret',
        region: '  US-East-1  ', bucket: 'my-bucket',
      })
      expect(adapter).to.be.instanceOf(AmazonS3Adapter)
      // Verify via getS3Url which includes the region
      const url = (adapter as any).getS3Url('test.pdf')
      expect(url).to.include('us-east-1')
    })
  })

  // -------------------------------------------------------------------------
  // validateFilePayload (private)
  // -------------------------------------------------------------------------
  describe('validateFilePayload (private)', () => {
    it('should throw StorageValidationError for missing buffer', () => {
      const adapter = createAdapter()
      try {
        ;(adapter as any).validateFilePayload({ buffer: null, documentPath: 'path', mimeType: 'text/plain' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError for missing path', () => {
      const adapter = createAdapter()
      try {
        ;(adapter as any).validateFilePayload({ buffer: Buffer.from('test'), documentPath: '', mimeType: 'text/plain' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError for missing mimeType', () => {
      const adapter = createAdapter()
      try {
        ;(adapter as any).validateFilePayload({ buffer: Buffer.from('test'), documentPath: 'path', mimeType: '' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should not throw for valid payload', () => {
      const adapter = createAdapter()
      expect(() => {
        ;(adapter as any).validateFilePayload({
          buffer: Buffer.from('test'), documentPath: 'path', mimeType: 'text/plain',
        })
      }).to.not.throw()
    })
  })

  // -------------------------------------------------------------------------
  // getS3Url (private)
  // -------------------------------------------------------------------------
  describe('getS3Url (private)', () => {
    it('should construct proper S3 URL', () => {
      const adapter = createAdapter()
      const url = (adapter as any).getS3Url('folder/file.pdf')
      expect(url).to.equal('https://my-bucket.s3.us-east-1.amazonaws.com/folder/file.pdf')
    })

    it('should URL-encode path components', () => {
      const adapter = createAdapter()
      const url = (adapter as any).getS3Url('folder/file name.pdf')
      expect(url).to.include('file%20name.pdf')
    })

    it('should not encode forward slashes in path', () => {
      const adapter = createAdapter()
      const url = (adapter as any).getS3Url('a/b/c/file.pdf')
      expect(url).to.include('a/b/c/file.pdf')
    })

    it('should encode special characters in path components', () => {
      const adapter = createAdapter()
      const url = (adapter as any).getS3Url('folder/file#1.pdf')
      expect(url).to.include('file%231.pdf')
    })
  })

  // -------------------------------------------------------------------------
  // extractKeyFromUrl (private)
  // -------------------------------------------------------------------------
  describe('extractKeyFromUrl (private)', () => {
    it('should extract key from valid S3 URL', () => {
      const adapter = createAdapter()
      const key = (adapter as any).extractKeyFromUrl(
        'https://my-bucket.s3.us-east-1.amazonaws.com/folder/file.pdf',
      )
      expect(key).to.equal('folder/file.pdf')
    })

    it('should throw StorageValidationError for invalid URL', () => {
      const adapter = createAdapter()
      try {
        ;(adapter as any).extractKeyFromUrl('https://other-bucket.s3.us-west-2.amazonaws.com/file.pdf')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should decode URL-encoded characters', () => {
      const adapter = createAdapter()
      const key = (adapter as any).extractKeyFromUrl(
        'https://my-bucket.s3.us-east-1.amazonaws.com/folder/file%20name.pdf',
      )
      expect(key).to.equal('folder/file name.pdf')
    })

    it('should remove trailing slashes', () => {
      const adapter = createAdapter()
      const key = (adapter as any).extractKeyFromUrl(
        'https://my-bucket.s3.us-east-1.amazonaws.com/folder/',
      )
      expect(key).to.equal('folder')
    })

    it('should handle deeply nested paths', () => {
      const adapter = createAdapter()
      const key = (adapter as any).extractKeyFromUrl(
        'https://my-bucket.s3.us-east-1.amazonaws.com/a/b/c/d/file.pdf',
      )
      expect(key).to.equal('a/b/c/d/file.pdf')
    })
  })

  // -------------------------------------------------------------------------
  // uploadDocumentToStorageService
  // -------------------------------------------------------------------------
  describe('uploadDocumentToStorageService', () => {
    it('should throw StorageValidationError for invalid payload', async () => {
      const adapter = createAdapter()
      try {
        await adapter.uploadDocumentToStorageService({
          buffer: null as any, documentPath: '', mimeType: '', isVersioned: false,
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should upload successfully and return URL', async () => {
      const adapter = createAdapter()
      const s3Stub = sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().resolves({ Key: 'folder/file.pdf' }),
      })

      const result = await adapter.uploadDocumentToStorageService({
        buffer: Buffer.from('test'), documentPath: 'folder/file.pdf',
        mimeType: 'application/pdf', isVersioned: false,
      })

      expect(result.statusCode).to.equal(200)
      expect(result.data).to.include('my-bucket.s3.us-east-1.amazonaws.com')
      expect(result.data).to.include('folder/file.pdf')
    })

    it('should throw StorageUploadError when result has no Key', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().resolves({ Key: null }),
      })

      try {
        await adapter.uploadDocumentToStorageService({
          buffer: Buffer.from('test'), documentPath: 'path',
          mimeType: 'text/plain', isVersioned: false,
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })

    it('should throw StorageUploadError on S3 SDK error', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().rejects(new Error('Network error')),
      })

      try {
        await adapter.uploadDocumentToStorageService({
          buffer: Buffer.from('test'), documentPath: 'path',
          mimeType: 'text/plain', isVersioned: false,
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // updateBuffer
  // -------------------------------------------------------------------------
  describe('updateBuffer', () => {
    it('should throw StorageNotFoundError when document has no S3 URL', async () => {
      const adapter = createAdapter()
      try {
        await adapter.updateBuffer(Buffer.from('test'), { s3: undefined } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should update buffer successfully', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().resolves({ Key: 'folder/file.pdf' }),
      })

      const result = await adapter.updateBuffer(Buffer.from('new-content'), {
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/folder/file.pdf' },
        mimeType: 'text/plain',
      } as any)

      expect(result.statusCode).to.equal(200)
      expect(result.data).to.include('folder/file.pdf')
    })

    it('should throw StorageUploadError when upload response missing key', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'upload').returns({
        promise: sinon.stub().resolves({ Key: null }),
      })

      try {
        await adapter.updateBuffer(Buffer.from('test'), {
          s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/folder/file.pdf' },
          mimeType: 'text/plain',
        } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getBufferFromStorageService
  // -------------------------------------------------------------------------
  describe('getBufferFromStorageService', () => {
    it('should throw StorageNotFoundError when S3 URL not found', async () => {
      const adapter = createAdapter()
      try {
        await adapter.getBufferFromStorageService({ s3: undefined } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should get buffer for current version (no version param)', async () => {
      const adapter = createAdapter()
      const testBuffer = Buffer.from('file content')
      sinon.stub((adapter as any).s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: testBuffer }),
      })

      const result = await adapter.getBufferFromStorageService({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/folder/file.pdf' },
      } as any)

      expect(result.statusCode).to.equal(200)
      expect(result.data).to.equal(testBuffer)
    })

    it('should get buffer for version 0 (same as current)', async () => {
      const adapter = createAdapter()
      const testBuffer = Buffer.from('content')
      sinon.stub((adapter as any).s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: testBuffer }),
      })

      const result = await adapter.getBufferFromStorageService({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
      } as any, 0)

      expect(result.statusCode).to.equal(200)
    })

    it('should get buffer for specific version', async () => {
      const adapter = createAdapter()
      const testBuffer = Buffer.from('version content')
      sinon.stub((adapter as any).s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: testBuffer }),
      })

      const result = await adapter.getBufferFromStorageService({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
        versionHistory: [
          { s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/v0.pdf' } },
          { s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/v1.pdf' } },
        ],
      } as any, 1)

      expect(result.statusCode).to.equal(200)
    })

    it('should throw StorageNotFoundError for version without URL', async () => {
      const adapter = createAdapter()
      try {
        await adapter.getBufferFromStorageService({
          s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
          versionHistory: [{}],
        } as any, 1)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageDownloadError when response has no body', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: null }),
      })

      try {
        await adapter.getBufferFromStorageService({
          s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
        } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageDownloadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getMultipartUploadId
  // -------------------------------------------------------------------------
  describe('getMultipartUploadId', () => {
    it('should return upload ID on success', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'createMultipartUpload').returns({
        promise: sinon.stub().resolves({ UploadId: 'upload-123' }),
      })

      const result = await adapter.getMultipartUploadId('path/file.pdf', 'application/pdf')
      expect(result.statusCode).to.equal(200)
      expect(result.data?.uploadId).to.equal('upload-123')
    })

    it('should throw MultipartUploadError when no UploadId returned', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'createMultipartUpload').returns({
        promise: sinon.stub().resolves({ UploadId: null }),
      })

      try {
        await adapter.getMultipartUploadId('path/file.pdf', 'application/pdf')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('should throw MultipartUploadError on SDK failure', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'createMultipartUpload').returns({
        promise: sinon.stub().rejects(new Error('AWS error')),
      })

      try {
        await adapter.getMultipartUploadId('path', 'text/plain')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // generatePresignedUrlForPart
  // -------------------------------------------------------------------------
  describe('generatePresignedUrlForPart', () => {
    it('should return presigned URL and part number', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'getSignedUrlPromise').resolves('https://presigned-url.com')

      const result = await adapter.generatePresignedUrlForPart('path/file.pdf', 1, 'upload-123')
      expect(result.statusCode).to.equal(200)
      expect(result.data?.url).to.equal('https://presigned-url.com')
      expect(result.data?.partNumber).to.equal(1)
    })

    it('should throw PresignedUrlError on failure', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'getSignedUrlPromise').rejects(new Error('failed'))

      try {
        await adapter.generatePresignedUrlForPart('path', 1, 'uid')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // completeMultipartUpload
  // -------------------------------------------------------------------------
  describe('completeMultipartUpload', () => {
    it('should return URL on success', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'completeMultipartUpload').returns({
        promise: sinon.stub().resolves({ Key: 'folder/file.pdf' }),
      })

      const result = await adapter.completeMultipartUpload('path', 'uid', [
        { ETag: 'etag1', PartNumber: 1 },
      ])
      expect(result.statusCode).to.equal(200)
      expect(result.data?.url).to.include('folder/file.pdf')
    })

    it('should throw MultipartUploadError when Key missing', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'completeMultipartUpload').returns({
        promise: sinon.stub().resolves({ Key: null }),
      })

      try {
        await adapter.completeMultipartUpload('path', 'uid', [])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // generatePresignedUrlForDirectUpload
  // -------------------------------------------------------------------------
  describe('generatePresignedUrlForDirectUpload', () => {
    it('should return presigned URL for direct upload', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'getSignedUrlPromise').resolves('https://put-presigned.com')

      const result = await adapter.generatePresignedUrlForDirectUpload('folder/file.pdf')
      expect(result.statusCode).to.equal(200)
      expect(result.data?.url).to.equal('https://put-presigned.com')
    })

    it('should throw PresignedUrlError on failure', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'getSignedUrlPromise').rejects(new Error('failed'))

      try {
        await adapter.generatePresignedUrlForDirectUpload('path')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getSignedUrl
  // -------------------------------------------------------------------------
  describe('getSignedUrl', () => {
    it('should throw StorageNotFoundError when document is null', async () => {
      const adapter = createAdapter()
      try {
        await adapter.getSignedUrl(null as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageNotFoundError when s3 URL not found', async () => {
      const adapter = createAdapter()
      try {
        await adapter.getSignedUrl({ s3: undefined } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should return signed URL for current version', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'getSignedUrlPromise').resolves('https://signed.com/file')

      const result = await adapter.getSignedUrl({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
      } as any)

      expect(result.statusCode).to.equal(200)
      expect(result.data).to.equal('https://signed.com/file')
    })

    it('should use version-specific URL when version provided', async () => {
      const adapter = createAdapter()
      sinon.stub((adapter as any).s3, 'getSignedUrlPromise').resolves('https://signed.com/v1')

      const result = await adapter.getSignedUrl({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
        versionHistory: [
          { s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/v0.pdf' } },
          { s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/v1.pdf' } },
        ],
      } as any, 1)

      expect(result.statusCode).to.equal(200)
    })

    it('should include content disposition when fileName provided', async () => {
      const adapter = createAdapter()
      const getSignedStub = sinon.stub((adapter as any).s3, 'getSignedUrlPromise').resolves('https://signed.com')

      await adapter.getSignedUrl({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
        extension: '.pdf',
      } as any, undefined, 'myfile')

      const params = getSignedStub.firstCall.args[1]
      expect(params.ResponseContentDisposition).to.include('attachment')
      expect(params.ResponseContentDisposition).to.include('myfile')
    })

    it('should use custom expiration time', async () => {
      const adapter = createAdapter()
      const getSignedStub = sinon.stub((adapter as any).s3, 'getSignedUrlPromise').resolves('https://signed.com')

      await adapter.getSignedUrl({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
      } as any, undefined, undefined, 7200)

      const params = getSignedStub.firstCall.args[1]
      expect(params.Expires).to.equal(7200)
    })
  })
})
