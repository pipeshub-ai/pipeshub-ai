import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs'
import os from 'os'
import path from 'path'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import { Container } from 'inversify'
import { createStorageRouter } from '../../../src/modules/storage/routes/storage.routes'
import { StorageController } from '../../../src/modules/storage/controllers/storage.controller'
import AmazonS3Adapter from '../../../src/modules/storage/providers/s3.provider'
import LocalStorageAdapter from '../../../src/modules/storage/providers/local-storage.provider'
import { vendorFailure } from '../../../src/modules/storage/providers/vendor-failure'
import { DocumentModel } from '../../../src/modules/storage/schema/document.schema'
import { endpoint as ENDPOINTS_KEY, storageEtcdPaths } from '../../../src/modules/storage/constants/constants'
import { StorageUploadError } from '../../../src/libs/errors/storage.errors'
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { Logger } from '../../../src/libs/services/logger.service'
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service'
import { scopedStorageServiceJwtGenerator } from '../../../src/libs/utils/createJwt'
import { DefaultStorageConfig } from '../../../src/modules/tokens_manager/services/cm.service'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import { FakeBackend } from '../tokens_manager/routes/connectors-http-harness'
import { FakeKeyValueStore } from '../knowledge_base/routes/kb-http-harness'
import { useInertWriteState } from '../../support/inert-write-state'
import { captureLogRecords, newRequestId, recordsOf, type LogRecord } from '../../helpers/log-capture'

// The real storage router on the local disk adapter, with request context, access line and error middleware, over
// HTTP. A storage failure is the error middleware's one line, whose cause names the vendor's status and code; the
// vendor's text (it quotes buckets, paths and file names) is on no line.

const JWT_SECRET = 'n5d-storage-jwt-secret'
const SCOPED_JWT_SECRET = 'n5d-storage-scoped-secret'
const ORG_ID = '64d000000000000000000d01'
const USER_ID = '64d0000000000000000000d2'
const MOUNT = 'n5d-storage-mount'
const VENDOR_TEXT = 'n5d-vendor-text Access Denied for bucket n5d-bucket key org/report.pdf'

/** An AWS SDK v3 refusal as the S3 provider wraps it. */
const awsRefusal = (): StorageUploadError => {
  const sdk = Object.assign(new Error(VENDOR_TEXT), { name: 'AccessDenied', $metadata: { httpStatusCode: 403 } })
  return new StorageUploadError('Failed to upload document to S3', vendorFailure(sdk))
}

const level = (r: LogRecord): string => (String(r.level).toLowerCase() === 'warning' ? 'warn' : String(r.level).toLowerCase())
const failureLines = (lines: LogRecord[]): LogRecord[] => lines.filter((r) => r.message === 'http.request.error')
const otherLinesAtInfoOrAbove = (lines: LogRecord[]): LogRecord[] =>
  lines.filter((r) => ['error', 'warn', 'info'].includes(level(r)) && !String(r.message).startsWith('http.request.'))
const written = (lines: LogRecord[]): string =>
  JSON.stringify(lines, (_k, v) => (v instanceof Error ? { message: v.message, stack: v.stack, cause: (v as any).cause } : v))
const causeOf = (line: LogRecord): unknown => (line.metadata.error as { cause?: unknown }).cause

describe('Storage routes log each failure once, with the vendor code and never its text', function () {
  this.timeout(30000)
  useInertWriteState()
  let backend: FakeBackend
  let kv: FakeKeyValueStore
  let server: http.Server
  let baseUrl: string
  let home: string
  let records: LogRecord[]
  const documents = new Map<string, InstanceType<typeof DocumentModel>>()
  const auth = (): string => `Bearer ${scopedStorageServiceJwtGenerator(ORG_ID, SCOPED_JWT_SECRET, USER_ID)}`

  /** Sends one request and waits for its access line; returns the request's lines. */
  const send = async (
    method: string,
    route: string,
    body?: FormData | Buffer,
    headers: Record<string, string | undefined> = {},
  ): Promise<{ status: number; body: Record<string, any>; lines: LogRecord[] }> => {
    const id = newRequestId('n5d')
    const all = Object.entries({ authorization: auth(), 'x-request-id': id, ...headers }).filter(([, v]) => v !== undefined)
    const res = await fetch(`${baseUrl}${route}`, { method, headers: Object.fromEntries(all) as Record<string, string>, body })
    const text = await res.text()
    const deadline = Date.now() + 3000
    while (Date.now() < deadline && !recordsOf(records, id).some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setTimeout(resolve, 5))
    }
    return { status: res.status, body: text ? (JSON.parse(text) as Record<string, any>) : {}, lines: recordsOf(records, id) }
  }

  const fileForm = (name = 'report.pdf'): FormData => {
    const form = new FormData()
    form.append('file', new Blob(['%PDF-1.4 tiny'], { type: 'application/pdf' }), name)
    return form
  }

  const upload = () => {
    const form = fileForm()
    form.append('documentPath', `PipesHub/KnowledgeBase/private/${USER_ID}`)
    form.append('isVersionedFile', 'true')
    form.append('documentName', 'report')
    return send('POST', '/internal/upload', form)
  }

  /** One stored document, uploaded through the real local adapter. */
  const stored = async (): Promise<string> => {
    const r = await upload()
    expect(r.status, JSON.stringify(r.body)).to.equal(200)
    return String(r.body._id)
  }

  const mountRoot = (): string =>
    path.join(home, process.platform === 'darwin' ? 'Library' : process.platform === 'win32' ? 'AppData' : '.local', MOUNT)

  /** The one failure line: from the error middleware, at WARN, `unavailable`, and nothing else at INFO or above. */
  const expectOneUnavailableLine = (lines: LogRecord[]): LogRecord => {
    const failure = failureLines(lines)
    expect(failure.map((x) => [level(x), x.error_class, x.status])).to.deep.equal([['warn', 'unavailable', 503]])
    expect(otherLinesAtInfoOrAbove(lines)).to.deep.equal([])
    expect(written(lines)).to.not.contain('n5d-vendor-text')
    return failure[0]!
  }

  let savedSecretKey: string | undefined
  before(() => {
    savedSecretKey = process.env.SECRET_KEY
    process.env.SECRET_KEY = process.env.SECRET_KEY || 'n5d-storage-secret-key'
  })
  after(() => {
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY
    else process.env.SECRET_KEY = savedSecretKey
  })

  beforeEach(async () => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'n5d-storage-home-'))
    sinon.stub(os, 'homedir').returns(home)
    backend = new FakeBackend()
    await backend.start()
    backend.on('GET', '/api/v1/configurationManager/internal/storageConfig', {
      status: 200,
      body: { storageType: 'local', mountName: MOUNT },
    })
    registerUpstreams({ nodejs: [backend.url] })
    kv = new FakeKeyValueStore()
    kv.values.set(storageEtcdPaths, JSON.stringify({ storageType: 'local' }))
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: backend.url }, storage: { endpoint: backend.url } }))

    const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
    const container = new Container()
    const defaultConfig: DefaultStorageConfig = { storageType: 'local', endpoint: backend.url }
    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(kv as unknown as KeyValueStoreService)
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens))
    container
      .bind<StorageController>('StorageController')
      .toConstantValue(new StorageController(defaultConfig, Logger.getInstance(), kv as unknown as KeyValueStoreService, SCOPED_JWT_SECRET))

    documents.clear()
    sinon.stub(DocumentModel, 'create').callsFake((async (info: Record<string, unknown>) => {
      const doc = new DocumentModel(info)
      sinon.stub(doc, 'save').callsFake(async () => {
        documents.set(String(doc._id), doc)
        return doc
      })
      documents.set(String(doc._id), doc)
      return doc
    }) as unknown as typeof DocumentModel.create)
    // The repository reloads the row it updates; a read asks for the lean row.
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: { _id: unknown }) => {
      const doc = documents.get(String(filter._id)) ?? null
      return Object.assign(Promise.resolve(doc), { lean: () => ({ exec: async () => doc?.toObject() ?? null }) })
    }) as unknown as typeof DocumentModel.findOne)
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: { _id: unknown }) => {
      documents.delete(String(filter._id))
      return Promise.resolve({ acknowledged: true, deletedCount: 1 })
    }) as unknown as typeof DocumentModel.deleteOne)

    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    app.use('/api/v1/document', createStorageRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = await new Promise<http.Server>((resolve) => {
      const listening = app.listen(0, '127.0.0.1', () => resolve(listening))
    })
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1/document`
    // The controller caches the storage config for the whole process.
    kv.changed(storageEtcdPaths)
    records = captureLogRecords()
  })

  afterEach(async () => {
    kv.changed(storageEtcdPaths)
    sinon.restore()
    registerUpstreams({})
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await backend.stop()
    fs.rmSync(home, { recursive: true, force: true })
  })

  describe('a write storage refuses', () => {
    it('an upload the vendor refuses is a 503 with one WARN whose cause is the vendor status and code', async () => {
      sinon.stub(LocalStorageAdapter.prototype, 'uploadDocumentToStorageService').rejects(awsRefusal())

      const r = await upload()

      expect(r.status).to.equal(503)
      const failure = expectOneUnavailableLine(r.lines)
      expect(causeOf(failure)).to.deep.equal({ status: 403, code: 'AccessDenied' })
      expect(r.lines.map((x) => x.message)).to.not.include('Writing a file to storage failed')
    })

    it('an upload storage answers with a failure is a 503 with one WARN whose cause is the answered status', async () => {
      sinon.stub(LocalStorageAdapter.prototype, 'uploadDocumentToStorageService').resolves({ statusCode: 500, msg: 'n5d-vendor-text' } as never)

      const r = await upload()

      expect(r.status).to.equal(503)
      expect(causeOf(expectOneUnavailableLine(r.lines))).to.deep.equal({ status: 500 })
    })

    it('an upload whose disk refuses the folder is one WARN whose cause is the OS code, never the mount path', async () => {
      // A plain file where the mount folder belongs: every attempt to make it fails.
      fs.mkdirSync(path.dirname(mountRoot()), { recursive: true })
      fs.writeFileSync(mountRoot(), '')

      const r = await upload()

      expect(r.status).to.equal(503)
      const failure = expectOneUnavailableLine(r.lines)
      expect(causeOf(failure)).to.have.property('code').that.matches(/^E[A-Z]+$/)
      expect(written(r.lines)).to.not.contain(home)
    })

    it('a buffer the vendor refuses is a 503 with one WARN; no "Failed to upload buffer" line', async () => {
      const id = await stored()
      sinon.stub(LocalStorageAdapter.prototype, 'updateBuffer').rejects(awsRefusal())

      const r = await send('PUT', `/internal/${id}/buffer`, fileForm())

      expect(r.status).to.equal(503)
      expect(causeOf(expectOneUnavailableLine(r.lines))).to.deep.equal({ status: 403, code: 'AccessDenied' })
    })

    it('a buffer storage answers with a failure is one WARN whose cause is the answered status', async () => {
      const id = await stored()
      sinon.stub(LocalStorageAdapter.prototype, 'updateBuffer').resolves({ statusCode: 507, msg: 'n5d-vendor-text' } as never)

      const r = await send('PUT', `/internal/${id}/buffer`, fileForm())

      expect(r.status).to.equal(503)
      expect(causeOf(expectOneUnavailableLine(r.lines))).to.deep.equal({ status: 507 })
    })

    it('a buffer handler that throws is the error middleware\'s one line, not also the route\'s', async () => {
      const id = await stored()
      sinon.stub(StorageController.prototype, 'createDocumentBuffer').rejects(new Error('n5d-vendor-text'))

      const r = await send('PUT', `/internal/${id}/buffer`, fileForm())

      expect(r.status).to.equal(500)
      expect(r.lines.filter((x) => level(x) === 'error').map((x) => x.message)).to.deep.equal(['http.request.error'])
      expect(otherLinesAtInfoOrAbove(r.lines)).to.deep.equal([])
    })
  })

  describe('a removal storage refuses', () => {
    it('a purge whose stored file cannot be removed is a 503 with one WARN whose cause is the vendor code; the document stays', async () => {
      const id = await stored()
      sinon.stub(LocalStorageAdapter.prototype, 'deleteObject').rejects(awsRefusal())

      const r = await send('DELETE', `/internal/${id}/purge`)

      expect(r.status).to.equal(503)
      expect(causeOf(expectOneUnavailableLine(r.lines))).to.deep.equal({ status: 403, code: 'AccessDenied' })
      expect(documents.has(id)).to.equal(true)
    })
  })

  describe('a hard delete', () => {
    it('removes the files with one DEBUG naming the document, never its path', async () => {
      const id = await stored()

      const r = await send('DELETE', `/internal/${id}?hard=true`)

      expect(r.status).to.equal(200)
      const mine = r.lines.filter((x) => String(x.message).startsWith('Hard-delete'))
      expect(mine.map((x) => [level(x), x.message, x.metadata])).to.deep.equal([
        ['debug', 'Hard-delete: blob cleanup done', { document_id: id }],
      ])
      expect(otherLinesAtInfoOrAbove(r.lines)).to.deep.equal([])
    })

    it('whose files storage refuses to remove is one WARN with the vendor status and code; the record still goes', async () => {
      const id = await stored()
      sinon.stub(LocalStorageAdapter.prototype, 'deleteTree').rejects(awsRefusal())

      const r = await send('DELETE', `/internal/${id}?hard=true`)

      expect(r.status).to.equal(200)
      expect(r.lines.filter((x) => level(x) !== 'debug' && !String(x.message).startsWith('http.request.'))).to.deep.equal([
        r.lines.find((x) => x.message === 'Hard-delete: blob cleanup failed; removing the record anyway'),
      ])
      const warn = r.lines.find((x) => x.message === 'Hard-delete: blob cleanup failed; removing the record anyway')!
      expect([level(warn), warn.status, warn.metadata]).to.deep.equal(['warn', 403, { document_id: id, code: 'AccessDenied' }])
      expect(written(r.lines)).to.not.contain('n5d-vendor-text')
      expect(documents.has(id)).to.equal(false)
    })
  })

  describe('a paid result storage refuses', () => {
    it('a result write the vendor refuses is a 503 with one WARN whose cause is the vendor code', async () => {
      const presigned = await send(
        'POST',
        '/internal/paid-results/presign',
        Buffer.from(JSON.stringify({ recordId: 'rec-1', files: [{ path: `ocr_page/${'a'.repeat(64)}.json`, mode: 'put' }] })),
        { 'content-type': 'application/json' },
      )
      expect(presigned.status, JSON.stringify(presigned.body)).to.equal(200)
      const file = presigned.body.files[0]
      sinon.stub(LocalStorageAdapter.prototype, 'writeObjectIfAbsent').rejects(awsRefusal())

      // The signed URL carries its own authorization.
      const url = new URL(file.url)
      const r = await send('PUT', url.pathname.replace('/api/v1/document', '') + url.search, Buffer.from('{}'), {
        authorization: undefined,
        ...file.headers,
      })

      expect(r.status).to.equal(503)
      expect(causeOf(expectOneUnavailableLine(r.lines))).to.deep.equal({ status: 403, code: 'AccessDenied' })
    })
  })

  describe('a read of a local file', () => {
    it('a document store that fails is one ERROR whose cause is the store\'s error; no line of its own', async () => {
      const id = await stored()
      ;(DocumentModel.findOne as sinon.SinonStub).callsFake(() => ({
        lean: () => ({ exec: () => Promise.reject(new Error('n5d-store-down')) }),
      }))

      const r = await send('GET', `/internal/${id}/download`)

      expect(r.status).to.equal(500)
      expect(r.lines.filter((x) => level(x) === 'error').map((x) => [x.message, x.error_class])).to.deep.equal([
        ['http.request.error', 'internal'],
      ])
      expect(causeOf(failureLines(r.lines)[0]!)).to.include({ message: 'n5d-store-down' })
      expect(otherLinesAtInfoOrAbove(r.lines)).to.deep.equal([])
    })

    it('a document store that drops its connection is one WARN unavailable, still answered 500', async () => {
      const id = await stored()
      // As the mongodb driver builds a MongoNetworkError: the socket error, with its code, is the cause.
      const socket = Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:27017'), { code: 'ECONNREFUSED' })
      const dropped = Object.assign(new Error('connection closed'), { name: 'MongoNetworkError', cause: socket })
      ;(DocumentModel.findOne as sinon.SinonStub).callsFake(() => ({
        lean: () => ({ exec: () => Promise.reject(dropped) }),
      }))

      const r = await send('GET', `/internal/${id}/download`)

      expect(r.status).to.equal(500)
      expect(failureLines(r.lines).map((x) => [level(x), x.error_class, x.status])).to.deep.equal([['warn', 'unavailable', 500]])
      expect(otherLinesAtInfoOrAbove(r.lines)).to.deep.equal([])
    })

    it('a file gone from the disk is one WARN not_found whose cause is the OS code; no path on any line', async () => {
      const id = await stored()
      fs.rmSync(mountRoot(), { recursive: true, force: true })

      const r = await send('GET', `/internal/${id}/download`)

      expect(r.status).to.equal(404)
      const failure = failureLines(r.lines)
      expect(failure.map((x) => [level(x), x.error_class])).to.deep.equal([['warn', 'not_found']])
      expect(causeOf(failure[0]!)).to.deep.equal({ code: 'ENOENT' })
      expect(otherLinesAtInfoOrAbove(r.lines)).to.deep.equal([])
      expect(written(r.lines)).to.not.contain(home)
    })
  })

  describe('a write that succeeds', () => {
    it('writes no storage line at INFO or above, and no file URL on any line, in development too', async () => {
      const saved = process.env.NODE_ENV
      process.env.NODE_ENV = 'development'
      try {
        // The adapter's start-up lines (development only) are its lifecycle, not an operation.
        const operations = (lines: LogRecord[]) =>
          lines.filter((x) => !/^(Local storage adapter initialized|Mount point ")/.test(String(x.message)))
        const r = await upload()
        expect(r.status).to.equal(200)
        expect(otherLinesAtInfoOrAbove(operations(r.lines))).to.deep.equal([])
        expect(written(operations(r.lines))).to.not.contain(`/api/v1/document/${String(r.body._id)}/download`)
        expect(written(operations(r.lines))).to.not.contain(home)

        const update = await send('PUT', `/internal/${String(r.body._id)}/buffer`, fileForm())
        expect(update.status).to.equal(200)
        expect(otherLinesAtInfoOrAbove(operations(update.lines))).to.deep.equal([])
        expect(written(operations(update.lines))).to.not.contain('file://')
      } finally {
        if (saved === undefined) delete process.env.NODE_ENV
        else process.env.NODE_ENV = saved
      }
    })
  })

  describe('a read storage refuses', () => {
    const S3_TEXT =
      'n5o-vendor-text User: arn:aws:iam::123456789012:user/n5o is not authorized to perform s3:GetObject on resource arn:aws:s3:::n5o-bucket/org/report.pdf'
    const s3Refusal = () => Object.assign(new Error(S3_TEXT), { name: 'AccessDenied', $metadata: { httpStatusCode: 403 } })
    const s3Url = 'https://n5o-bucket.s3.us-east-1.amazonaws.com/org/report.pdf'
    const realS3 = (): AmazonS3Adapter =>
      new AmazonS3Adapter({ accessKeyId: 'AKIAIOSFODNN7EXAMPLE', secretAccessKey: 'n5o-secret', region: 'us-east-1', bucket: 'n5o-bucket' })
    const filesUnder = (dir: string): string[] =>
      fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? filesUnder(path.join(dir, e.name)) : [path.join(dir, e.name)]))
    const folderNamed = (dir: string, name: string): string => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        if (!e.isDirectory()) continue
        const full = path.join(dir, e.name)
        if (e.name === name) return full
        try {
          return folderNamed(full, name)
        } catch {
          // not under this one
        }
      }
      throw new Error(`no folder ${name} under ${dir}`)
    }

    /** The one failure line: ERROR `internal` 500, as before; returns the error it logged. */
    const expectOneInternalLine = (r: { status: number; lines: LogRecord[] }): Record<string, any> => {
      expect(r.status).to.equal(500)
      const failure = failureLines(r.lines)
      expect(failure.map((x) => [level(x), x.error_class, x.status])).to.deep.equal([['error', 'internal', 500]])
      return failure[0]!.metadata.error as Record<string, any>
    }

    /** A record's paid result, stored through the real local adapter; returns the record's folder. */
    const paidResult = async (record: string): Promise<string> => {
      const put = await send('PUT', `/internal/paid-results/${record}/files/manifests/a/b.json`, Buffer.from('{}'), {
        'content-type': 'application/octet-stream',
      })
      expect(put.status, JSON.stringify(put.body)).to.equal(200)
      return folderNamed(mountRoot(), record)
    }

    // Root ignores file modes, so the chmod cases cannot fail as root.
    const skipAsRoot = (t: Mocha.Context) => {
      if (process.getuid?.() === 0) t.skip()
    }

    /** Runs `read` with `folder` unreadable, restoring it so the home folder can be removed. */
    const withUnreadable = async <T>(folder: string, read: () => Promise<T>): Promise<T> => {
      fs.chmodSync(folder, 0o000)
      try {
        return await read()
      } finally {
        fs.chmodSync(folder, 0o755)
      }
    }

    it('local: a stored file the OS refuses to read (GET buffer) logs its code, never its path', async function () {
      skipAsRoot(this)
      const id = await stored()
      for (const f of filesUnder(mountRoot())) fs.chmodSync(f, 0o000)
      const r = await send('GET', `/internal/${id}/buffer`)
      expect(expectOneInternalLine(r).metadata).to.include({ code: 'EACCES' })
      expect(written(r.lines)).to.not.contain(home)
    })

    it('S3: getObject refused (GET buffer) logs the vendor status and code, never its text', async () => {
      const id = await stored()
      const s3 = realS3()
      sinon.stub((s3 as any).s3, 'getObject').returns({ promise: sinon.stub().rejects(s3Refusal()) })
      sinon.stub(LocalStorageAdapter.prototype, 'getBufferFromStorageService').callsFake(() => s3.getBufferFromStorageService({ s3: { url: s3Url } } as any))
      const r = await send('GET', `/internal/${id}/buffer`)
      expect(expectOneInternalLine(r).metadata).to.include({ statusCode: 403, code: 'AccessDenied' })
      expect(written(r.lines)).to.not.contain('n5o-vendor-text')
    })

    it('S3: signing refused (GET download) logs the vendor status and code, never its text', async () => {
      const id = await stored()
      const s3 = realS3()
      sinon.stub((s3 as any).s3, 'getSignedUrlPromise').rejects(s3Refusal())
      sinon.stub(LocalStorageAdapter.prototype, 'getSignedUrl').callsFake(() => s3.getSignedUrl({ s3: { url: s3Url } } as any))
      const r = await send('GET', `/internal/${id}/download`)
      expect(expectOneInternalLine(r).metadata).to.include({ statusCode: 403, code: 'AccessDenied' })
      expect(written(r.lines)).to.not.contain('n5o-vendor-text')
    })

    it('local: storage that cannot be opened on a read (GET buffer) logs the code, never the fs text', async () => {
      const id = await stored()
      fs.rmSync(mountRoot(), { recursive: true, force: true })
      fs.writeFileSync(mountRoot(), '')
      kv.changed(storageEtcdPaths)
      const r = await send('GET', `/internal/${id}/buffer`)
      // The wrap's own mountPath key is still on this line (a path key, not the vendor's text): a follow-up.
      expect(expectOneInternalLine(r).metadata).to.include({ code: 'EEXIST' })
      expect(written(r.lines)).to.not.contain('file already exists')
    })

    it('local: a paid-results listing the OS refuses logs its code, never the folder path', async function () {
      skipAsRoot(this)
      const folder = await paidResult('rec1')
      const r = await withUnreadable(folder, () => send('GET', '/internal/paid-results/rec1'))
      const error = expectOneInternalLine(r)
      expect(error.name).to.equal('StorageDownloadError')
      expect(error.metadata).to.include({ code: 'EACCES' })
      expect(written(r.lines)).to.not.contain(home)
    })

    it('local: a paid-result file in an unreadable folder logs its code, never its path', async function () {
      skipAsRoot(this)
      const folder = await paidResult('rec2')
      const r = await withUnreadable(folder, () => send('GET', '/internal/paid-results/rec2/files/manifests/a/b.json'))
      const error = expectOneInternalLine(r)
      expect(error.name).to.equal('StorageDownloadError')
      expect(error.metadata).to.include({ code: 'EACCES' })
      expect(written(r.lines)).to.not.contain(home)
    })
  })

  describe('a purge the vendor fails for some keys', () => {
    it('logs no object key or vendor text (the S3 batch-removal error)', async () => {
      sinon
        .stub(LocalStorageAdapter.prototype, 'deleteTree')
        .rejects(new StorageUploadError('Failed to delete some objects from S3', { originalError: 'org/n5o-key.pdf: AccessDenied n5o-vendor-text' }))
      const r = await send('DELETE', '/internal/paid-results/org')
      expect(r.status).to.equal(500)
      expect(failureLines(r.lines)).to.have.length(1)
      expect(written(r.lines)).to.not.contain('n5o-key.pdf')
      expect(written(r.lines)).to.not.contain('n5o-vendor-text')
    })
  })
})
