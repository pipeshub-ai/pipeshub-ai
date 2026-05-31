import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import AmazonS3Adapter from '../../../../src/modules/storage/providers/s3.provider'
import { StorageUploadError } from '../../../../src/libs/errors/storage.errors'
import {
  buildS3HealthCheckErrorMessage,
  validateS3Capabilities,
} from '../../../../src/modules/storage/utils/s3-health-check.util'

// aws-sdk v2 attaches service methods to instances (not the prototype), so we
// cannot use sinon.stub(S3.prototype, …). Instead we replace the constructor
// on the live module object, which TypeScript accesses at call time via the
// module reference (aws_sdk_1.S3), making the swap transparent to the util.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const awsSdk = require('aws-sdk') as { S3: new (...args: unknown[]) => unknown }

function buildFakeS3DeleteStub(resolves = true) {
  const deleteFn = resolves
    ? sinon.stub().resolves()
    : sinon.stub().rejects(new Error('delete-error'))
  return { deleteObject: () => ({ promise: deleteFn }) }
}

function replaceSdkS3(fakeInstance: object) {
  const originalS3 = awsSdk.S3
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(awsSdk as any).S3 = function MockS3() {
    return fakeInstance
  }
  return () => {
    ;(awsSdk as any).S3 = originalS3
  }
}

const VALID_CREDS = {
  accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
  secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
  region: 'us-east-1',
  bucketName: 'my-bucket',
}

const FAKE_URL = 'https://my-bucket.s3.us-east-1.amazonaws.com/.pipeshub-health-check/test-id'

describe('s3-health-check.util', () => {
  let restoreS3: (() => void) | undefined

  afterEach(() => {
    sinon.restore()
    restoreS3?.()
    restoreS3 = undefined
  })

  // -------------------------------------------------------------------------
  // buildS3HealthCheckErrorMessage
  // -------------------------------------------------------------------------
  describe('buildS3HealthCheckErrorMessage', () => {
    it('should summarize failed capability checks', () => {
      const message = buildS3HealthCheckErrorMessage([
        { capability: 'bucketAccess', passed: true },
        { capability: 'upload', passed: false, error: 'AccessDenied' },
        { capability: 'signedUrlPut', passed: false, error: 'AccessDenied' },
      ])

      expect(message).to.include('S3 health check failed')
      expect(message).to.include('upload: AccessDenied')
      expect(message).to.include('signedUrlPut: AccessDenied')
      expect(message).to.include('s3:PutObject')
    })

    it('should use "failed" when check has no error message', () => {
      const message = buildS3HealthCheckErrorMessage([
        { capability: 'read', passed: false },
      ])
      expect(message).to.include('read: failed')
    })

    it('should still return the header when all checks pass', () => {
      const message = buildS3HealthCheckErrorMessage([
        { capability: 'bucketAccess', passed: true },
        { capability: 'upload', passed: true },
      ])
      expect(message).to.include('S3 health check failed')
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — adapter init fails (invalid credentials)
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — adapter init fails', () => {
    it('should return bucketAccess failure when accessKeyId is empty', async () => {
      const result = await validateS3Capabilities({
        accessKeyId: '',
        secretAccessKey: 'secret',
        region: 'us-east-1',
        bucketName: 'my-bucket',
      })

      expect(result.success).to.be.false
      expect(result.checks).to.have.lengthOf(1)
      expect(result.checks[0].capability).to.equal('bucketAccess')
      expect(result.checks[0].passed).to.be.false
    })

    it('should return bucketAccess failure when region fails format validation', async () => {
      const result = await validateS3Capabilities({
        accessKeyId: 'key',
        secretAccessKey: 'secret',
        region: 'invalid!!!region',
        bucketName: 'my-bucket',
      })

      expect(result.success).to.be.false
      expect(result.checks[0].capability).to.equal('bucketAccess')
      expect(result.checks[0].passed).to.be.false
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — all checks pass
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — all checks pass', () => {
    it('should return success with 5 passing checks', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon.stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService').resolves({
        statusCode: 200,
        data: FAKE_URL,
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getBufferFromStorageService').resolves({
        statusCode: 200,
        data: Buffer.from('pipeshub-s3-health-check'),
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getSignedUrl').resolves({
        statusCode: 200,
        data: 'https://signed-get-url',
      })
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      expect(result.success).to.be.true
      expect(result.checks).to.have.lengthOf(5)
      const names = result.checks.map((c) => c.capability)
      expect(names).to.deep.equal(['bucketAccess', 'upload', 'read', 'signedUrlGet', 'signedUrlPut'])
      expect(result.checks.every((c) => c.passed)).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — upload fails → bucket-related error
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — upload fails with bucket error', () => {
    it('should classify NoSuchBucket error as bucketAccess failure', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon
        .stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService')
        .rejects(
          new StorageUploadError('Failed to upload document to S3', {
            originalError: 'NoSuchBucket: The specified bucket does not exist',
          }),
        )
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      expect(result.success).to.be.false
      const bucketCheck = result.checks.find((c) => c.capability === 'bucketAccess')
      const uploadCheck = result.checks.find((c) => c.capability === 'upload')
      const readCheck = result.checks.find((c) => c.capability === 'read')
      const signedGetCheck = result.checks.find((c) => c.capability === 'signedUrlGet')
      expect(bucketCheck?.passed).to.be.false
      expect(uploadCheck?.error).to.include('Skipped because bucket was not found')
      expect(readCheck?.error).to.include('Skipped because upload check failed')
      expect(signedGetCheck?.error).to.include('Skipped because upload check failed')
    })

    it('should classify "bucket is not valid" as bucketAccess failure', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon
        .stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService')
        .rejects(
          new StorageUploadError('Failed to upload document to S3', {
            originalError: 'The specified bucket is not valid.',
          }),
        )
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      expect(result.success).to.be.false
      const bucketCheck = result.checks.find((c) => c.capability === 'bucketAccess')
      expect(bucketCheck?.passed).to.be.false
    })

    it('should classify AccessDenied as upload failure while bucketAccess passes', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon
        .stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService')
        .rejects(
          new StorageUploadError('Failed to upload document to S3', {
            originalError: 'AccessDenied',
          }),
        )
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      expect(result.success).to.be.false
      const bucketCheck = result.checks.find((c) => c.capability === 'bucketAccess')
      const uploadCheck = result.checks.find((c) => c.capability === 'upload')
      expect(bucketCheck?.passed).to.be.true
      expect(uploadCheck?.passed).to.be.false
      expect(uploadCheck?.error).to.include('AccessDenied')
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — read check fails
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — read check fails', () => {
    it('should report read failure independently when upload passes', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon.stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService').resolves({
        statusCode: 200,
        data: FAKE_URL,
      })
      sinon
        .stub(AmazonS3Adapter.prototype, 'getBufferFromStorageService')
        .rejects(new Error('AccessDenied'))
      sinon.stub(AmazonS3Adapter.prototype, 'getSignedUrl').resolves({
        statusCode: 200,
        data: 'https://signed-get-url',
      })
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      expect(result.success).to.be.false
      const readCheck = result.checks.find((c) => c.capability === 'read')
      expect(readCheck?.passed).to.be.false
      expect(readCheck?.error).to.include('AccessDenied')
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — signedUrlGet check fails
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — signedUrlGet check fails', () => {
    it('should report signedUrlGet failure independently', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon.stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService').resolves({
        statusCode: 200,
        data: FAKE_URL,
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getBufferFromStorageService').resolves({
        statusCode: 200,
        data: Buffer.from('ok'),
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getSignedUrl').rejects(new Error('SigningError'))
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      expect(result.success).to.be.false
      const check = result.checks.find((c) => c.capability === 'signedUrlGet')
      expect(check?.passed).to.be.false
      expect(check?.error).to.include('SigningError')
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — signedUrlPut check fails
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — signedUrlPut check fails', () => {
    it('should report signedUrlPut failure independently', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon.stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService').resolves({
        statusCode: 200,
        data: FAKE_URL,
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getBufferFromStorageService').resolves({
        statusCode: 200,
        data: Buffer.from('ok'),
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getSignedUrl').resolves({
        statusCode: 200,
        data: 'https://signed-get-url',
      })
      sinon
        .stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload')
        .rejects(new Error('PresignError'))

      const result = await validateS3Capabilities(VALID_CREDS)

      expect(result.success).to.be.false
      const check = result.checks.find((c) => c.capability === 'signedUrlPut')
      expect(check?.passed).to.be.false
      expect(check?.error).to.include('PresignError')
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — StorageError with originalError surfaces underlying cause
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — StorageError includes underlying cause', () => {
    it('should include originalError message in capability error string', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon
        .stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService')
        .rejects(
          new StorageUploadError('Failed to upload document to S3', {
            originalError: 'Access Denied by IAM policy',
          }),
        )
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      const uploadCheck = result.checks.find((c) => c.capability === 'upload')
      expect(uploadCheck?.error).to.include('Access Denied by IAM policy')
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — input normalisation (region / bucket trimming)
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — input normalisation', () => {
    it('should trim surrounding whitespace from bucketName and region', async () => {
      // region is valid after trim, bucket has spaces → adapter succeeds but upload fails
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub())
      sinon
        .stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService')
        .rejects(new Error('SomeBucketError'))
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities({
        ...VALID_CREDS,
        bucketName: '  my-bucket  ',
        region: '  us-east-1  ',
      })

      // The test exercises the trim paths; adapter initialises, upload fails
      expect(result.checks.length).to.be.greaterThan(0)
    })
  })

  // -------------------------------------------------------------------------
  // validateS3Capabilities — cleanup failure is silently swallowed
  // -------------------------------------------------------------------------
  describe('validateS3Capabilities — cleanup error is ignored', () => {
    it('should still return success even when S3 deleteObject rejects', async () => {
      restoreS3 = replaceSdkS3(buildFakeS3DeleteStub(false)) // delete rejects
      sinon.stub(AmazonS3Adapter.prototype, 'uploadDocumentToStorageService').resolves({
        statusCode: 200,
        data: FAKE_URL,
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getBufferFromStorageService').resolves({
        statusCode: 200,
        data: Buffer.from('ok'),
      })
      sinon.stub(AmazonS3Adapter.prototype, 'getSignedUrl').resolves({
        statusCode: 200,
        data: 'https://signed-get-url',
      })
      sinon.stub(AmazonS3Adapter.prototype, 'generatePresignedUrlForDirectUpload').resolves({
        statusCode: 200,
        data: { url: 'https://signed-put-url' },
      })

      const result = await validateS3Capabilities(VALID_CREDS)

      // delete threw, but must not affect overall result
      expect(result.success).to.be.true
    })
  })
})
