import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import nock from 'nock'
import { Logger } from '../../../../src/libs/services/logger.service'
import { ConnectorServiceCommand } from '../../../../src/libs/commands/connector_service/connector.service.command'
import {
  PlaceholderResultWithMetadata,
  createPlaceholderDocument,
  logUploadBatchEnd,
  processUploadsInBackground,
  uploadFileToSignedUrl,
  uploadNextVersionToStorage,
} from '../../../../src/modules/knowledge_base/utils/utils'
import { STORAGE_WRITE_FAILED_MESSAGE } from '../../../../src/modules/storage/constants/constants'
import { classifyError } from '../../../../src/libs/utils/error-class'
import { captureLogRecords, inRequestContext, newRequestId, recordsOf } from '../../../helpers/log-capture'

/**
 * The upload path logs no vendor text and no file name, and a batch is one `kb.upload.batch.end` line at the
 * level of what failed (docs/logging.md, "Knowledge base (Node)").
 */

type Row = Record<string, any>

const STORAGE = 'https://bucket.storage.test'
const STORAGE_SERVICE = 'http://127.0.0.1:19293'
const PY = 'http://python:8088/api/v1/kb/kb-1/upload'
// What S3 answers a refused signed PUT with: the canonical request names the bucket path and the file.
const VENDOR_MARKER = 'canonical-request-marker-5521'
const FILE_NAME = 'Jane Doe offer letter.pdf'
// The signed URL's query is a bearer credential for the bucket path.
const SIGNATURE = 'sig=abc'
const VENDOR_XML = `<?xml version="1.0"?><Error><Code>SignatureDoesNotMatch</Code><CanonicalRequest>PUT /org/${FILE_NAME} ${VENDOR_MARKER}</CanonicalRequest></Error>`

const level = (r: Row): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}
const atLeastInfo = (r: Row): boolean => ['error', 'warn', 'info'].includes(level(r))

const placeholder = (key: string, upload?: () => Promise<void>): PlaceholderResultWithMetadata => ({
  placeholderResult: { documentId: `doc-${key}`, documentName: FILE_NAME, upload },
  metadata: {
    file: {} as any,
    filePath: `/uploads/${key}/${FILE_NAME}`,
    fileName: FILE_NAME,
    extension: 'pdf',
    correctMimeType: 'application/pdf',
    key,
    webUrl: `/record/${key}`,
    validLastModified: Date.now(),
    size: 100,
  },
})

describe('knowledge base upload logging', () => {
  let records: Row[]
  let requestId: string

  beforeEach(() => {
    records = captureLogRecords()
    requestId = newRequestId('kb-upload-logging')
  })
  afterEach(() => {
    nock.cleanAll()
    sinon.restore()
  })

  const mine = (): Row[] => recordsOf(records, requestId)
  const run = <T>(fn: () => Promise<T>): Promise<T> => inRequestContext(requestId, fn)

  describe('uploadFileToSignedUrl', () => {
    it('a refused PUT is one WARN with the status; the vendor body and file name are in no line and not in the error', async () => {
      nock(STORAGE).put('/org/upload').query(true).reply(403, VENDOR_XML, { 'content-type': 'application/xml' })

      const error = await run(() =>
        uploadFileToSignedUrl(Buffer.from('bytes'), 'application/pdf', `${STORAGE}/org/upload?${SIGNATURE}`, 'doc-1'),
      ).then(
        () => expect.fail('a 403 must reject'),
        (e: Error) => e,
      )

      expect(mine().filter(atLeastInfo).map((r) => [level(r), r.message, r.status, r.error_class])).to.deep.equal([
        ['warn', 'File upload to signed URL failed', 403, 'forbidden'],
      ])
      expect(error.message).to.equal('Upload to the signed URL was refused')
      expect((error as { cause?: unknown }).cause).to.deep.equal({ status: 403 })
      for (const text of [JSON.stringify(mine()), error.message]) {
        expect(text).to.not.include(VENDOR_MARKER)
        expect(text).to.not.include(FILE_NAME)
        expect(text).to.not.include(SIGNATURE)
      }
    })

    it('an accepted PUT writes nothing above DEBUG', async () => {
      nock(STORAGE).put('/org/upload').reply(200)

      await run(() => uploadFileToSignedUrl(Buffer.from('bytes'), 'application/pdf', `${STORAGE}/org/upload`, 'doc-1'))

      expect(mine().filter(atLeastInfo)).to.deep.equal([])
    })
  })

  describe('createPlaceholderDocument', () => {
    const storedEndpoints = () => ({ get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: STORAGE_SERVICE } })) })
    const createPlaceholder = (kv: { get: sinon.SinonStub }) =>
      run(() =>
        createPlaceholderDocument(
          { user: { userId: 'user-1' }, headers: {} } as any,
          { buffer: Buffer.from('x'), originalname: FILE_NAME, mimetype: 'application/pdf', size: 1 } as any,
          FILE_NAME,
          false,
          kv as any,
          { storageType: 'local', endpoint: STORAGE_SERVICE } as any,
          'storage-token',
        ),
      ).then(
        () => expect.fail('the placeholder must fail'),
        (e: Error & { cause?: unknown }) => e,
      )

    it("a storage-service failure logs its status and class, never the service's body", async () => {
      nock(STORAGE_SERVICE)
        .post('/api/v1/document/internal/upload')
        .reply(500, { error: { detail: VENDOR_MARKER } })

      const error = await createPlaceholder(storedEndpoints())

      const lines = mine().filter((r) => r.message === 'Error creating placeholder document')
      expect(lines.map((r) => [level(r), r.status, r.error_class, r.metadata?.error])).to.deep.equal([
        ['warn', 500, 'upstream_5xx', undefined],
      ])
      expect(JSON.stringify(mine())).to.not.include(VENDOR_MARKER)
      // The plain message keeps the refusal as its cause, so the per-file line classifies it.
      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
      expect(classifyError(error)).to.equal('upstream_5xx')
    })

    for (const [name, kv] of [
      ['the key-value store read rejects', () => ({ get: sinon.stub().rejects(new Error('etcd unavailable')) })],
      ['the endpoints document is corrupt', () => ({ get: sinon.stub().resolves('{not json') })],
    ] as const) {
      it(`${name}: one ERROR with the stack, no file name, and the plain message to the client`, async () => {
        const error = await createPlaceholder(kv())

        const errors = mine().filter((r) => level(r) === 'error')
        expect(errors.map((r) => [r.message, r.error_class])).to.deep.equal([['Error creating placeholder document', 'internal']])
        expect(errors[0]?.metadata?.error?.stack).to.be.a('string').and.not.equal('')
        expect(JSON.stringify(mine())).to.not.include(FILE_NAME)
        expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
        expect(classifyError(error)).to.equal('internal')
      })
    }

    it('storage answers 2xx with no document id: one ERROR, not two', async () => {
      nock(STORAGE_SERVICE).post('/api/v1/document/internal/upload').reply(200, {})

      const error = await createPlaceholder(storedEndpoints())

      expect(mine().filter((r) => level(r) === 'error').map((r) => r.message)).to.deep.equal([
        'Storage saved the file but returned no document id',
      ])
      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
    })
  })

  describe('a signed-URL upload started by createPlaceholderDocument', () => {
    const nodeEnv = process.env.NODE_ENV
    afterEach(() => {
      process.env.NODE_ENV = nodeEnv
    })

    it('fails with the plain storage message, carrying the refusal as its cause; the signed URL is in no line', async () => {
      // Development mode is where the signed URL used to be logged.
      process.env.NODE_ENV = 'development'
      nock(STORAGE_SERVICE)
        .post('/api/v1/document/internal/upload')
        .reply(301, '', { location: `${STORAGE}/org/upload?${SIGNATURE}`, 'x-document-id': 'doc-9' })
      nock(STORAGE).put('/org/upload').query(true).reply(403, VENDOR_XML)
      nock(STORAGE_SERVICE).post('/api/v1/document/internal/doc-9/abortDirectUpload').reply(200, {})
      const kv = { get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: STORAGE_SERVICE } })) }

      const placeholderDoc = await run(() =>
        createPlaceholderDocument(
          { user: { userId: 'user-1' }, headers: {} } as any,
          { buffer: Buffer.from('x'), originalname: FILE_NAME, mimetype: 'application/pdf', size: 1 } as any,
          FILE_NAME,
          false,
          kv as any,
          { storageType: 's3', endpoint: STORAGE_SERVICE } as any,
          'storage-token',
        ),
      )
      const error = await run(() => placeholderDoc.upload!()).then(
        () => expect.fail('a refused PUT must reject'),
        (e: Error & { cause?: Error & { cause?: unknown } }) => e,
      )

      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
      expect(error.cause?.message).to.equal('Upload to the signed URL was refused')
      expect(error.cause?.cause).to.deep.equal({ status: 403 })
      expect(JSON.stringify(mine())).to.not.include(VENDOR_MARKER)
      for (const text of [JSON.stringify(mine()), error.message, String(error.cause?.message)]) {
        expect(text).to.not.include(SIGNATURE)
      }
    })
  })

  describe('uploadNextVersionToStorage', () => {
    it('a refused upload propagates with no line of its own beyond the call line', async () => {
      nock(STORAGE_SERVICE).post('/api/v1/document/internal/doc-1/uploadNextVersion').reply(500, { error: VENDOR_MARKER })
      const kv = { get: sinon.stub().resolves(JSON.stringify({ storage: { endpoint: STORAGE_SERVICE } })) }

      const failed = await run(() =>
        uploadNextVersionToStorage(
          {} as any,
          { buffer: Buffer.from('x'), originalname: FILE_NAME, mimetype: 'application/pdf', size: 1 } as any,
          'doc-1',
          kv as any,
          { storageType: 'local', endpoint: STORAGE_SERVICE } as any,
          'storage-token',
        ),
      ).then(
        () => false,
        () => true,
      )

      expect(failed).to.equal(true)
      expect(mine().filter(atLeastInfo).filter((r) => r.message !== 'upstream.call.end')).to.deep.equal([])
    })
  })

  describe('processUploadsInBackground with logUploadBatchEnd, as the upload stream pairs them', () => {
    const logger = Logger.getInstance({ service: 'kb-upload-logging-test' })
    const batch = (results: PlaceholderResultWithMetadata[]) =>
      run(async () => {
        const startedAt = Date.now()
        const { failure, ...counts } = await processUploadsInBackground(results, 'org-1', 'kb-1', startedAt, PY, {}, logger, sinon.stub(), { secret: 'test-scoped-secret', userId: 'user-1' })
        logUploadBatchEnd(logger, { files: results.length, ...counts }, startedAt, failure)
        return counts
      })
    /** The lines above DEBUG, as [level, message, outcome, files, succeeded, failed, status, error_class]. */
    const visible = (): unknown[][] =>
      mine()
        .filter(atLeastInfo)
        .map((r) => [
          level(r),
          r.message,
          r.outcome,
          r.metadata?.files,
          r.metadata?.succeeded,
          r.metadata?.failed,
          r.status,
          r.error_class,
        ])
    const indexing = () => sinon.stub(ConnectorServiceCommand.prototype, 'execute')
    const refused = () => Promise.reject(new Error('Upload failed'))

    // No line names a file: each file's outcome is its own SSE frame.
    const expectNoFileName = (): void => {
      expect(JSON.stringify(mine())).to.not.include(FILE_NAME)
    }

    it('all files indexed: one INFO line, outcome=success, with the duration', async () => {
      indexing().resolves({ statusCode: 200, data: {} } as any)

      expect(await batch([placeholder('a'), placeholder('b', () => Promise.resolve())])).to.deep.equal({ succeeded: 2, failed: 0 })

      expect(visible()).to.deep.equal([['info', 'kb.upload.batch.end', 'success', 2, 2, 0, undefined, undefined]])

      expectNoFileName()
      expect(mine().find((r) => r.message === 'kb.upload.batch.end')?.duration_ms).to.be.a('number')
    })

    it('one storage upload refused: one INFO line, outcome=partial; the file is DEBUG with its class', async () => {
      indexing().resolves({ statusCode: 200, data: {} } as any)

      await batch([placeholder('a'), placeholder('b', refused)])

      expect(visible()).to.deep.equal([['info', 'kb.upload.batch.end', 'partial', 2, 1, 1, undefined, undefined]])

      expectNoFileName()
      expect(
        mine()
          .filter((r) => r.message === 'Background upload failed')
          .map((r) => [level(r), r.error_class]),
      ).to.deep.equal([['debug', 'internal']])
    })

    it('every storage upload refused: one INFO line, outcome=failure, and no indexing call', async () => {
      const execute = indexing()

      await batch([placeholder('a', refused), placeholder('b', refused)])

      expect(execute.called).to.equal(false)
      expect(visible()).to.deep.equal([['info', 'kb.upload.batch.end', 'failure', 2, 0, 2, undefined, undefined]])
      expectNoFileName()
    })

    it('files the indexing service skipped as duplicates: INFO, outcome=partial', async () => {
      indexing().resolves({ statusCode: 200, data: { skippedFiles: [{ filePath: `/uploads/b/${FILE_NAME}` }] } } as any)

      await batch([placeholder('a'), placeholder('b')])

      expect(visible()).to.deep.equal([['info', 'kb.upload.batch.end', 'partial', 2, 1, 1, undefined, undefined]])

      expectNoFileName()
    })

    for (const [status, errorClass] of [
      [400, 'bad_request'],
      [500, 'upstream_5xx'],
      [503, 'unavailable'],
    ] as const) {
      it(`the indexing service answers ${status}: one WARN line with status and error_class=${errorClass}`, async () => {
        indexing().resolves({ statusCode: status, data: { detail: VENDOR_MARKER }, msg: VENDOR_MARKER } as any)

        await batch([placeholder('a')])

        expect(visible()).to.deep.equal([['warn', 'kb.upload.batch.end', 'failure', 1, 0, 1, status, errorClass]])

        expectNoFileName()
        expect(JSON.stringify(mine())).to.not.include(VENDOR_MARKER)
      })
    }

    it('our own failure (a TypeError) is one ERROR line with the stack', async () => {
      indexing().rejects(new TypeError('Cannot read properties of undefined'))

      await batch([placeholder('a')])

      expect(visible()).to.deep.equal([['error', 'kb.upload.batch.end', 'failure', 1, 0, 1, undefined, 'internal']])

      expectNoFileName()
      const line = mine().find((r) => r.message === 'kb.upload.batch.end')
      expect(line?.metadata?.error?.stack).to.include('TypeError')
    })

    it('the indexing service unreachable is WARN unavailable, not ERROR', async () => {
      // What fetch throws, through the command, for a refused connection.
      const refusedConnection = Object.assign(new Error('connect ECONNREFUSED 10.0.0.7:8088'), { code: 'ECONNREFUSED' })
      indexing().rejects(Object.assign(new TypeError('fetch failed'), { cause: refusedConnection }))

      await batch([placeholder('a')])

      expect(visible()).to.deep.equal([['warn', 'kb.upload.batch.end', 'failure', 1, 0, 1, undefined, 'unavailable']])

      expectNoFileName()
    })
  })
})
