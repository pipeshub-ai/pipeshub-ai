import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs/promises'
import { storageCause, vendorFailure } from '../../../../src/modules/storage/providers/vendor-failure'
import AmazonS3Adapter from '../../../../src/modules/storage/providers/s3.provider'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'
import { StorageError, StorageUploadError } from '../../../../src/libs/errors/storage.errors'
import { ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'
import { captureLogRecords, type LogRecord } from '../../../helpers/log-capture'
import { envGuard } from '../../../helpers/env-guard'

const TEXT = 'n5d-vendor-text bucket/org/report.pdf'

const awsError = (name: string, status: unknown): Error =>
  Object.assign(new Error(TEXT), { name, $metadata: { httpStatusCode: status } })
const azureError = (code: unknown, statusCode: unknown): Error =>
  Object.assign(new Error(TEXT), { name: 'RestError', code, statusCode })

const payload = { buffer: Buffer.from('x'), documentPath: 'org/report.pdf', mimeType: 'application/pdf', isVersioned: false }

/** The wrap's metadata without the hint every StorageUploadError adds, and the vendor's text it keeps apart. */
const wrapped = async (call: () => Promise<unknown>): Promise<{ metadata: Record<string, unknown>; vendorText?: string }> => {
  try {
    await call()
  } catch (error) {
    expect(error).to.be.instanceOf(StorageError)
    const metadata = { ...(error as StorageError).metadata }
    delete metadata.hint
    return { metadata, vendorText: (error as StorageError).vendorText }
  }
  throw new Error('expected the call to fail')
}

describe('vendorFailure', () => {
  it('reads an AWS SDK v3 error: the HTTP status and the error name', () => {
    expect(vendorFailure(awsError('AccessDenied', 403))).to.deep.equal({ originalError: TEXT, statusCode: 403, code: 'AccessDenied' })
  })

  it('reads an Azure RestError: its statusCode and its code, underscores included', () => {
    expect(vendorFailure(azureError('REQUEST_SEND_ERROR', 503))).to.deep.equal({
      originalError: TEXT,
      statusCode: 503,
      code: 'REQUEST_SEND_ERROR',
    })
  })

  it('reads a Node fs error: its code, and no status', () => {
    const fsError = Object.assign(new Error(`ENOENT: no such file or directory, open '/mnt/${TEXT}'`), { code: 'ENOENT' })
    expect(vendorFailure(fsError)).to.deep.equal({ originalError: fsError.message, code: 'ENOENT' })
  })

  it('keeps a code with digits after its first letter (Azure Md5Mismatch)', () => {
    expect(vendorFailure(azureError('Md5Mismatch', 400))).to.include({ code: 'Md5Mismatch' })
  })

  it('drops a code that could be text: a space, a dot, a colon, a leading digit or over 64 characters', () => {
    for (const code of ['Access Denied', 'Access.Denied', 'Denied:', '403Forbidden', 'A'.repeat(65), '']) {
      expect(vendorFailure(azureError(code, 403)), code).to.deep.equal({ originalError: TEXT, statusCode: 403 })
    }
  })

  it('falls back to the name only when the code is not a string', () => {
    expect(vendorFailure(Object.assign(new Error(TEXT), { name: 'SlowDown', code: 503 }))).to.deep.equal({
      originalError: TEXT,
      code: 'SlowDown',
    })
  })

  it('drops a status that is not an integer', () => {
    for (const status of ['403', 403.5, null, Number.NaN]) {
      expect(vendorFailure(awsError('AccessDenied', status)), String(status)).to.deep.equal({ originalError: TEXT, code: 'AccessDenied' })
    }
  })

  it('says "Unknown error" for a failure that is not an Error, and reads nothing from it', () => {
    expect(vendorFailure('n5d-vendor-text')).to.deep.equal({ originalError: 'Unknown error' })
    expect(vendorFailure(undefined)).to.deep.equal({ originalError: 'Unknown error' })
    expect(vendorFailure(null)).to.deep.equal({ originalError: 'Unknown error' })
  })
})

describe('storageCause', () => {
  it("reads a provider wrap's metadata, never its originalError", () => {
    const error = new StorageUploadError('Failed to upload document to S3', vendorFailure(awsError('AccessDenied', 403)))
    expect(storageCause(error)).to.deep.equal({ status: 403, code: 'AccessDenied' })
  })

  it('reads a vendor error that was not wrapped', () => {
    expect(storageCause(azureError('AuthorizationFailure', 403))).to.deep.equal({ status: 403, code: 'AuthorizationFailure' })
  })

  it("reads an adapter's answer: its status", () => {
    expect(storageCause({ statusCode: 500, msg: TEXT })).to.deep.equal({ status: 500 })
  })

  it('is empty for our own error with no vendor status or code, and for a value that is not an error', () => {
    expect(storageCause(new ServiceUnavailableError(TEXT))).to.deep.equal({})
    expect(storageCause(new StorageUploadError(TEXT, { originalError: TEXT }))).to.deep.equal({})
    expect(storageCause(TEXT)).to.deep.equal({})
    expect(storageCause(undefined)).to.deep.equal({})
  })

  it('checks the code a wrap metadata carries again', () => {
    expect(storageCause(new StorageUploadError(TEXT, { statusCode: 403, code: 'has space' }))).to.deep.equal({ status: 403 })
  })
})

// One wrap per provider: the 50 wraps share vendorFailure, so a sample guards the edit.
describe('provider wraps keep the vendor status and code', () => {
  const env = envGuard()
  let records: LogRecord[]
  beforeEach(() => {
    env.snapshot()
    records = captureLogRecords()
  })
  afterEach(() => {
    env.restore()
    sinon.restore()
  })

  const s3 = (): AmazonS3Adapter =>
    new AmazonS3Adapter({ accessKeyId: 'AKIAIOSFODNN7EXAMPLE', secretAccessKey: 'n5d-secret', region: 'us-east-1', bucket: 'my-bucket' })

  it('S3 upload', async () => {
    const adapter = s3()
    sinon.stub((adapter as any).s3, 'upload').returns({ promise: () => Promise.reject(awsError('AccessDenied', 403)) })
    expect(await wrapped(() => adapter.uploadDocumentToStorageService(payload))).to.deep.equal({
      metadata: { statusCode: 403, code: 'AccessDenied' },
      vendorText: TEXT,
    })
  })

  it('Azure upload', async () => {
    const proto = require('../../../../src/modules/storage/providers/azure.provider').default.prototype
    proto.logger = { info: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    proto.containerClient = {
      getBlockBlobClient: sinon.stub().returns({ uploadData: sinon.stub().rejects(azureError('AuthorizationFailure', 403)), url: 'u' }),
    }
    try {
      expect(await wrapped(() => proto.uploadDocumentToStorageService(payload))).to.deep.equal({
        metadata: { statusCode: 403, code: 'AuthorizationFailure' },
        vendorText: TEXT,
      })
    } finally {
      delete proto.containerClient
      delete proto.logger
    }
  })

  it('local write', async () => {
    const adapter = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    sinon.stub(adapter, 'ensureReady').resolves()
    sinon.stub(fs, 'mkdir').rejects(Object.assign(new Error(`EACCES: permission denied, mkdir '/mnt/${TEXT}'`), { code: 'EACCES' }))
    const { metadata, vendorText } = await wrapped(() => adapter.uploadDocumentToStorageService(payload))
    expect(metadata).to.deep.equal({ code: 'EACCES' })
    expect(vendorText).to.contain(TEXT)
  })

  it('an S3 upload that succeeds writes no line at INFO or above and no object URL, in development too', async () => {
    process.env.NODE_ENV = 'development'
    const adapter = s3()
    sinon.stub((adapter as any).s3, 'upload').returns({ promise: () => Promise.resolve({ Key: 'org/report.pdf' }) })
    records.length = 0

    const result = await adapter.uploadDocumentToStorageService(payload)

    expect(result.statusCode).to.equal(200)
    const mine = records.filter((r) => r.service === 'AmazonS3Adapter' || String(r.message).startsWith('S3 '))
    expect(mine.map((r) => [r.level, r.message])).to.deep.equal([['debug', 'S3 upload successful']])
    expect(JSON.stringify(mine)).to.not.contain(String(result.data))
  })

  it('an Azure upload that succeeds writes one DEBUG line and never its (SAS-signed) URL, in development too', async () => {
    process.env.NODE_ENV = 'development'
    const sasUrl = 'https://acct.blob.core.windows.net/files/org/report.pdf?sig=n5d-sas'
    const proto = require('../../../../src/modules/storage/providers/azure.provider').default.prototype
    const logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    proto.logger = logger
    proto.containerClient = { getBlockBlobClient: sinon.stub().returns({ uploadData: sinon.stub().resolves(), url: sasUrl }) }
    try {
      const result = await proto.uploadDocumentToStorageService(payload)

      expect(result).to.deep.equal({ statusCode: 200, data: sasUrl })
      expect(logger.debug.args).to.deep.equal([['Azure Blob Storage upload successful', { path: 'org/report.pdf' }]])
      expect([logger.info, logger.warn, logger.error].map((s) => s.callCount)).to.deep.equal([0, 0, 0])
    } finally {
      delete proto.containerClient
      delete proto.logger
    }
  })
})
