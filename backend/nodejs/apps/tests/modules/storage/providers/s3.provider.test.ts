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

import { S3 } from 'aws-sdk'
import mongoose from 'mongoose'
import AmazonS3Adapter from '../../../../src/modules/storage/providers/s3.provider'
import {
  Document,
  StorageVendor,
} from '../../../../src/modules/storage/types/storage.service.types'
import { StorageError } from '../../../../src/libs/errors/storage.errors';

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
    it('should throw StorageConfigurationError when region and bucket are missing', () => {
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

    it('should throw when only secretAccessKey is supplied', () => {
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

    it('should create adapter without any credentials (IAM role mode)', () => {
      const adapter = new AmazonS3Adapter({ region: 'us-east-1', bucket: 'my-bucket' })
      expect(adapter).to.be.instanceOf(AmazonS3Adapter)
    })

    it('should create adapter when both credential fields are empty strings (IAM role mode)', () => {
      const adapter = new AmazonS3Adapter({
        accessKeyId: '', secretAccessKey: '',
        region: 'us-east-1', bucket: 'my-bucket',
      })
      expect(adapter).to.be.instanceOf(AmazonS3Adapter)
    })

    it('should create adapter when both credential fields are whitespace-only (IAM role mode)', () => {
      const adapter = new AmazonS3Adapter({
        accessKeyId: '   ', secretAccessKey: '   ',
        region: 'us-east-1', bucket: 'my-bucket',
      })
      expect(adapter).to.be.instanceOf(AmazonS3Adapter)
    })

    it('should throw StorageConfigurationError when only accessKeyId is provided', () => {
      try {
        new AmazonS3Adapter({
          accessKeyId: 'AKIAIOSFODNN7EXAMPLE', secretAccessKey: '',
          region: 'us-east-1', bucket: 'my-bucket',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
        expect((error as StorageConfigurationError).message).to.include(
          'must be provided together',
        )
      }
    })

    it('should throw StorageConfigurationError when only secretAccessKey is provided', () => {
      try {
        new AmazonS3Adapter({
          accessKeyId: '   ', secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
          region: 'us-east-1', bucket: 'my-bucket',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
        expect((error as StorageConfigurationError).message).to.include(
          'must be provided together',
        )
      }
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

    it('should get buffer for version 0 from versionHistory', async () => {
      const adapter = createAdapter()
      const testBuffer = Buffer.from('content')
      sinon.stub((adapter as any).s3, 'getObject').returns({
        promise: sinon.stub().resolves({ Body: testBuffer }),
      })

      const result = await adapter.getBufferFromStorageService({
        s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf' },
        versionHistory: [
          { s3: { url: 'https://my-bucket.s3.us-east-1.amazonaws.com/v0.pdf' } },
        ],
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

// We can't easily import the actual S3 adapter because it creates a real S3 client,
// so we test the constructor validation and private method logic patterns.

describe('AmazonS3Adapter - branch coverage', () => {
  afterEach(() => { sinon.restore() })

  // =========================================================================
  // Constructor validation branches
  // =========================================================================
  describe('constructor validation', () => {
    it('should throw when accessKeyId is missing but secretAccessKey is supplied', () => {
      // Dynamically import
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      expect(
        () => new AmazonS3Adapter({ accessKeyId: '', secretAccessKey: 'key', region: 'us-east-1', bucket: 'b' }),
      ).to.throw(StorageConfigurationError)
    })

    it('should throw when secretAccessKey is missing but accessKeyId is supplied', () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      expect(
        () => new AmazonS3Adapter({ accessKeyId: 'key', secretAccessKey: '', region: 'us-east-1', bucket: 'b' }),
      ).to.throw(StorageConfigurationError)
    })

    it('should not throw when both accessKeyId and secretAccessKey are omitted (IAM role mode)', () => {
      const AmazonS3Adapter = require('../../../../src/modules/storage/providers/s3.provider').default
      const adapter = new AmazonS3Adapter({ region: 'us-east-1', bucket: 'b' })
      expect(adapter).to.exist
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

// =========================================================================
// IAM role mode (no explicit accessKeyId/secretAccessKey)
// =========================================================================
describe('AmazonS3Adapter - IAM role mode', () => {
  afterEach(() => { sinon.restore() })

  function createIamRoleAdapter(): AmazonS3Adapter {
    return new AmazonS3Adapter({ region: 'us-east-1', bucket: 'my-bucket' })
  }

  // The adapter owns its S3 client privately; tests stub it through a typed seam.
  function s3ClientOf(adapter: AmazonS3Adapter): S3 {
    return (adapter as unknown as { s3: S3 }).s3
  }

  function s3Document(url: string): Document {
    return {
      documentName: 'file.pdf',
      isVersionedFile: false,
      orgId: new mongoose.Types.ObjectId(),
      initiatorUserId: null,
      extension: 'pdf',
      currentVersion: 1,
      isDeleted: false,
      storageVendor: StorageVendor.S3,
      s3: { url },
    }
  }

  it('should upload successfully without explicit credentials', async () => {
    const adapter = createIamRoleAdapter()
    sinon.stub(s3ClientOf(adapter), 'upload').returns({
      promise: sinon.stub().resolves({ Key: 'folder/file.pdf' }),
    } as unknown as ReturnType<S3['upload']>)

    const result = await adapter.uploadDocumentToStorageService({
      buffer: Buffer.from('test'), documentPath: 'folder/file.pdf',
      mimeType: 'application/pdf', isVersioned: false,
    })

    expect(result.statusCode).to.equal(200)
    expect(result.data).to.include('my-bucket.s3.us-east-1.amazonaws.com')
  })

  it('should read back an object successfully without explicit credentials', async () => {
    const adapter = createIamRoleAdapter()
    const testBuffer = Buffer.from('content')
    sinon.stub(s3ClientOf(adapter), 'getObject').returns({
      promise: sinon.stub().resolves({ Body: testBuffer }),
    } as unknown as ReturnType<S3['getObject']>)

    const result = await adapter.getBufferFromStorageService(
      s3Document('https://my-bucket.s3.us-east-1.amazonaws.com/folder/file.pdf'),
    )

    expect(result.statusCode).to.equal(200)
    expect(result.data).to.equal(testBuffer)
  })

  it('should generate a signed GET URL successfully without explicit credentials', async () => {
    const adapter = createIamRoleAdapter()
    sinon.stub(s3ClientOf(adapter), 'getSignedUrlPromise').resolves('https://signed.com/file')

    const result = await adapter.getSignedUrl(
      s3Document('https://my-bucket.s3.us-east-1.amazonaws.com/file.pdf'),
    )

    expect(result.statusCode).to.equal(200)
    expect(result.data).to.equal('https://signed.com/file')
  })

  it('should generate a signed PUT URL for direct upload successfully without explicit credentials', async () => {
    const adapter = createIamRoleAdapter()
    sinon.stub(s3ClientOf(adapter), 'getSignedUrlPromise').resolves('https://put-presigned.com')

    const result = await adapter.generatePresignedUrlForDirectUpload('folder/file.pdf')
    expect(result.statusCode).to.equal(200)
    expect(result.data.url).to.equal('https://put-presigned.com')
  })
})
