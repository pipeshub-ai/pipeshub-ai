import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import nock from 'nock'
import {
  processUploadsInBackground,
  PlaceholderResultWithMetadata,
  uploadFileToSignedUrl,
  UPLOAD_STORAGE_CONCURRENCY,
} from '../../../../src/modules/knowledge_base/utils/utils'
import { ConnectorServiceCommand } from '../../../../src/libs/commands/connector_service/connector.service.command'

describe('Knowledge Base Utils - coverage', () => {
  afterEach(() => {
    sinon.restore()
    nock.cleanAll()
    nock.enableNetConnect()
  })

  describe('UPLOAD_STORAGE_CONCURRENCY constant', () => {
    it('should be a positive integer', () => {
      expect(UPLOAD_STORAGE_CONCURRENCY).to.be.a('number')
      expect(UPLOAD_STORAGE_CONCURRENCY).to.be.greaterThan(0)
      expect(Number.isInteger(UPLOAD_STORAGE_CONCURRENCY)).to.be.true
    })
  })

  describe('uploadFileToSignedUrl', () => {
    let originalFetch: typeof globalThis.fetch

    beforeEach(() => {
      originalFetch = globalThis.fetch
    })

    afterEach(() => {
      globalThis.fetch = originalFetch
    })

    it('should resolve on a successful upload (2xx response)', async () => {
      globalThis.fetch = sinon.stub().resolves({
        ok: true,
        status: 200,
      } as any)

      await uploadFileToSignedUrl(
        Buffer.from('test'),
        'application/pdf',
        'https://storage.example.com/upload',
        'doc-1',
        'test.pdf',
      )

      expect((globalThis.fetch as sinon.SinonStub).calledOnce).to.be.true
      const [url, opts] = (globalThis.fetch as sinon.SinonStub).firstCall.args
      expect(url).to.equal('https://storage.example.com/upload')
      expect(opts.method).to.equal('PUT')
    })

    it('should throw when the storage server returns a non-ok status', async () => {
      globalThis.fetch = sinon.stub().resolves({
        ok: false,
        status: 403,
        text: sinon.stub().resolves('Forbidden'),
      } as any)

      let err: Error | undefined
      try {
        await uploadFileToSignedUrl(
          Buffer.from('test'),
          'application/pdf',
          'https://storage.example.com/upload',
          'doc-1',
          'test.pdf',
        )
      } catch (e) {
        err = e as Error
      }
      expect(err).to.be.instanceOf(Error)
      expect(err?.message).to.include('403')
    })

    it('should throw when fetch itself rejects (network error)', async () => {
      globalThis.fetch = sinon.stub().rejects(new Error('ECONNREFUSED'))

      let err: Error | undefined
      try {
        await uploadFileToSignedUrl(
          Buffer.from('test'),
          'application/pdf',
          'https://storage.example.com/upload',
          'doc-1',
          'test.pdf',
        )
      } catch (e) {
        err = e as Error
      }
      expect(err).to.be.instanceOf(Error)
      expect(err?.message).to.equal('ECONNREFUSED')
    })
  })

  describe('processUploadsInBackground', () => {
    let mockLogger: any
    let publish: sinon.SinonStub

    const failedEvents = () =>
      publish.getCalls().filter((c) => c.args[0] === 'file:failed').map((c) => c.args[1])
    const successEvents = () =>
      publish.getCalls().filter((c) => c.args[0] === 'file:succeeded').map((c) => c.args[1])

    function createPlaceholderResult(options: {
      upload?: () => Promise<void>
      documentId?: string
      documentName?: string
      fileName?: string
      key?: string
      filePath?: string
    } = {}): PlaceholderResultWithMetadata {
      return {
        placeholderResult: {
          documentId: options.documentId || 'doc-1',
          documentName: options.documentName || 'test.pdf',
          upload: options.upload,
        },
        metadata: {
          file: { buffer: Buffer.from('test'), mimetype: 'application/pdf', originalname: 'test.pdf', size: 4 } as any,
          filePath: options.filePath || '/path/to/test.pdf',
          fileName: options.fileName || 'test.pdf',
          extension: '.pdf',
          correctMimeType: 'application/pdf',
          key: options.key || 'key-1',
          webUrl: 'http://example.com/test.pdf',
          validLastModified: Date.now(),
          size: 4,
        },
      }
    }

    const PY_URL = 'http://python/api/v1/kb/kb1/upload'

    beforeEach(() => {
      nock.disableNetConnect()
      mockLogger = {
        info: sinon.stub(),
        debug: sinon.stub(),
        warn: sinon.stub(),
        error: sinon.stub(),
      }
      publish = sinon.stub()
    })

    it('streams a file:failed (stage upload) for each storage-upload failure and returns counts', async () => {
      const results = [
        createPlaceholderResult({
          upload: () => Promise.reject(new Error('Upload failed')),
          key: 'failed-key',
        }),
      ]

      const counts = await processUploadsInBackground(
        results,
        'org-1', Date.now(),
        PY_URL,
        { Authorization: 'Bearer test' },
        mockLogger,
        publish,
      )

      const fe = failedEvents()
      expect(fe).to.have.length(1)
      expect(fe[0].stage).to.equal('upload')
      expect(counts).to.deep.equal({ succeeded: 0, failed: 1 })
    })

    it('warns and returns failed counts when zero uploads succeed', async () => {
      const results = [
        createPlaceholderResult({ upload: () => Promise.reject(new Error('fail')) }),
      ]

      const counts = await processUploadsInBackground(
        results,
        'org-1', Date.now(),
        PY_URL,
        {},
        mockLogger,
        publish,
      )

      expect(mockLogger.warn.called).to.be.true
      expect(counts).to.deep.equal({ succeeded: 0, failed: 1 })
    })

    describe('with the indexing service mocked', () => {
      it('streams file:succeeded for each indexed file on a clean 200', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: {} } as any)

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'ok-1' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        expect(failedEvents()).to.have.length(0)
        expect(successEvents()).to.have.length(1)
        expect(successEvents()[0].filePath).to.equal('/path/to/test.pdf')
        expect(counts).to.deep.equal({ succeeded: 1, failed: 0 })
      })

      it('streams file:failed (stage index) for python-reported failedFiles', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: { failedFiles: ['/path/to/test.pdf'] } } as any)

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'ok-1' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        expect(failedEvents()).to.have.length(1)
        expect(failedEvents()[0].stage).to.equal('index')
        expect(counts).to.deep.equal({ succeeded: 0, failed: 1 })
      })

      it('marks all uploaded files failed (stage index) on a non-200', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 500, msg: 'boom' } as any)

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'ok-1' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        expect(failedEvents()).to.have.length(1)
        expect(failedEvents()[0].stage).to.equal('index')
        expect(counts).to.deep.equal({ succeeded: 0, failed: 1 })
      })

      it('returns failed counts even when the indexing call throws', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .rejects(new Error('network down'))

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'ok-1' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        expect(counts.failed).to.be.greaterThan(0)
        expect(mockLogger.error.called).to.be.true
      })

      it('handles a mix of upload-failed, indexed, and python-failed files', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: { failedFiles: ['/path/py-failed.pdf'] } } as any)

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'ok-1', filePath: '/path/ok.pdf' }),
          createPlaceholderResult({ upload: () => Promise.reject(new Error('fail')), key: 'fail-1', filePath: '/path/upload-failed.pdf' }),
          createPlaceholderResult({ key: 'direct-1', filePath: '/path/py-failed.pdf' }), // direct upload, python fails it
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        const stages = failedEvents().map((e: any) => e.stage)
        expect(stages).to.include('upload')
        expect(stages).to.include('index')
        expect(successEvents()).to.have.length(1) // ok-1
        expect(counts).to.deep.equal({ succeeded: 1, failed: 2 })
      })

      // Regression: the indexing service may return a 200 that still reports
      // files it SKIPPED because a record with that name already exists in the
      // target location. These must surface as file:failed with a stable
      // DUPLICATE_NAME reason (not silently counted as succeeded).
      it('streams file:failed with reason DUPLICATE_NAME for python-reported skippedFiles', async () => {
        sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
          statusCode: 200,
          data: {
            skippedFiles: [
              { filePath: '/path/dup.pdf', name: 'dup.pdf', reason: 'DUPLICATE_NAME' },
            ],
          },
        } as any)

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'dup-1', filePath: '/path/dup.pdf' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        const fe = failedEvents()
        expect(fe).to.have.length(1)
        expect(fe[0].stage).to.equal('index')
        expect(fe[0].reason).to.equal('DUPLICATE_NAME')
        expect(successEvents()).to.have.length(0)
        expect(counts).to.deep.equal({ succeeded: 0, failed: 1 })
      })

      it('handles skippedFiles and failedFiles nested under data wrapper', async () => {
        sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
          statusCode: 200,
          data: {
            data: {
              failedFiles: ['/path/py-failed.pdf'],
              skippedFiles: [
                { filePath: '/path/dup.pdf', name: 'dup.pdf', reason: 'DUPLICATE_NAME' },
              ],
            },
          },
        } as any)

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'ok-1', filePath: '/path/ok.pdf' }),
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'pyfail-1', filePath: '/path/py-failed.pdf' }),
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'dup-1', filePath: '/path/dup.pdf' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        expect(successEvents()).to.have.length(1)
        expect(successEvents()[0].filePath).to.equal('/path/ok.pdf')
        expect(counts).to.deep.equal({ succeeded: 1, failed: 2 })
      })

      it('includes file extension in succeeded and failed events', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: {} } as any)

        const results = [
          createPlaceholderResult({
            upload: () => Promise.resolve(),
            key: 'ok-1',
            filePath: '/docs/report.pdf',
            fileName: 'report.pdf',
          }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        const se = successEvents()
        expect(se).to.have.length(1)
        expect(se[0].extension).to.equal('pdf')
        expect(counts.succeeded).to.equal(1)
      })

      it('derives extension from documentName when filePath has no extension', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: {} } as any)

        const results = [
          createPlaceholderResult({
            upload: () => Promise.resolve(),
            key: 'ok-1',
            filePath: '/docs/noext',
            documentName: 'report.docx',
          }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        const se = successEvents()
        expect(se).to.have.length(1)
        expect(se[0].extension).to.equal('docx')
        expect(counts.succeeded).to.equal(1)
      })

      it('includes extension in file:failed events for upload failures', async () => {
        const results = [
          createPlaceholderResult({
            upload: () => Promise.reject(new Error('Upload failed')),
            key: 'fail-1',
            filePath: '/docs/bad.xlsx',
            fileName: 'bad.xlsx',
          }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        const fe = failedEvents()
        expect(fe).to.have.length(1)
        expect(fe[0].extension).to.equal('xlsx')
        expect(counts.failed).to.equal(1)
      })

      it('handles files with no upload function (direct upload path)', async () => {
        sinon
          .stub(ConnectorServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: {} } as any)

        const results = [
          createPlaceholderResult({ key: 'direct-1', filePath: '/path/direct.pdf' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        expect(successEvents()).to.have.length(1)
        expect(counts).to.deep.equal({ succeeded: 1, failed: 0 })
      })

      it('handles empty placeholderResults array', async () => {
        const counts = await processUploadsInBackground(
          [],
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        expect(counts).to.deep.equal({ succeeded: 0, failed: 0 })
      })

      it('separates succeeded, python-failed, and skipped(duplicate) files in one batch', async () => {
        sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
          statusCode: 200,
          data: {
            failedFiles: ['/path/py-failed.pdf'],
            skippedFiles: [
              { filePath: '/path/dup.pdf', name: 'dup.pdf', reason: 'DUPLICATE_NAME' },
            ],
          },
        } as any)

        const results = [
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'ok-1', filePath: '/path/ok.pdf' }),
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'pyfail-1', filePath: '/path/py-failed.pdf' }),
          createPlaceholderResult({ upload: () => Promise.resolve(), key: 'dup-1', filePath: '/path/dup.pdf' }),
        ]

        const counts = await processUploadsInBackground(
          results,
          'org-1', Date.now(),
          PY_URL,
          {},
          mockLogger,
          publish,
        )

        const reasons = failedEvents().map((e: any) => e.reason)
        expect(reasons).to.include('DUPLICATE_NAME')
        expect(successEvents()).to.have.length(1)
        expect(successEvents()[0].filePath).to.equal('/path/ok.pdf')
        expect(counts).to.deep.equal({ succeeded: 1, failed: 2 })
      })
    })
  })
})
