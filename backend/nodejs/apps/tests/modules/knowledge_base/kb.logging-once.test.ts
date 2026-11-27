import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { LOG_CONTEXT_KEYS } from '../../../src/libs/context/request-context'
import { FileProcessorService } from '../../../src/libs/middlewares/file_processor/fp.service'
import { KB_UPLOAD_LIMITS } from '../../../src/modules/knowledge_base/constants/kb.constants'
import { captureLogRecords, newRequestId, recordsOf } from '../../helpers/log-capture'
import {
  ADMIN,
  FOLDER_ID,
  KB_ID,
  KB_ROUTES,
  KbHarness,
  KbRoute,
  RECORD_ID,
  call,
  pdfForm,
  sessionToken,
  sseEvents,
  startKbHarness,
} from './routes/kb-http-harness'

/**
 * The knowledge base routes write no line of their own: a request is its call lines, the error middleware's one
 * failure line and the access line, each naming the knowledge base and record from the path
 * (docs/logging.md, "Knowledge base (Node)").
 */

type Row = Record<string, any>

const KB_NAME = 'Payroll review Jane Doe 2026'
const FOLDER_NAME = 'Salary bands Jane Doe'
const STREAM_PATH = `/api/v1/stream/record/${RECORD_ID}`
const FILE_NAME = 'Jane Doe contract 2026.pdf'
// The only lines a proxied request writes at INFO or above.
const REQUEST_LINES = new Set(['upstream.call.end', 'http.request.error', 'http.request.end'])

const level = (r: Row): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}
const atLeastInfo = (r: Row): boolean => ['error', 'warn', 'info'].includes(level(r))
// The path ids a line carries; org_id and user_id come from the session, not the path.
const pathIds = (r: Row): Row =>
  Object.fromEntries(LOG_CONTEXT_KEYS.filter((key) => r[key] !== undefined).map((key) => [key, r[key]]))
// Only `:kbId` and `:recordId` are bound: a folder, record group or knowledge-hub parent id is not one of them.
const idsOf = (route: KbRoute): Row => ({
  ...(route.pattern.includes(':kbId') ? { kb_id: KB_ID } : {}),
  ...(route.pattern.includes(':recordId') ? { record_id: RECORD_ID } : {}),
})

describe('Knowledge base routes: a request is logged once, with its knowledge base and record', function () {
  this.timeout(20000)
  let h: KbHarness
  let records: Row[]
  let requestId = ''
  let responseText = ''

  beforeEach(async () => {
    h = await startKbHarness()
    records = captureLogRecords()
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  const mine = (): Row[] => recordsOf(records, requestId)
  const visible = (): Row[] => mine().filter(atLeastInfo)
  const byMessage = (message: string): Row[] => mine().filter((r) => r.message === message)
  // The shared file processor's own DEBUG lines name the files it parsed; they are not this module's.
  const knowledgeBaseLines = (): Row[] => mine().filter((r) => r.service !== 'FileProcessorService')

  /** Sends one request as the admin and returns its status once the access line is written. */
  const send = async (method: string, path: string, opts: { json?: unknown; form?: FormData } = {}): Promise<number> => {
    records.length = 0
    requestId = newRequestId('kb-logging-once')
    const res = await call(h, method, path, { ...opts, token: sessionToken(h, ADMIN), headers: { 'x-request-id': requestId } })
    responseText = res.text
    const deadline = Date.now() + 5000
    while (Date.now() < deadline && !mine().some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setImmediate(resolve))
    }
    return res.status
  }

  // Every route that calls the connector service: each handler used to log its failure at ERROR before passing it on.
  for (const route of KB_ROUTES.filter((r) => r.forwards)) {
    it(`${route.method} ${route.pattern}: a connector-service 500 writes no controller line, and no ERROR`, async () => {
      const [method, backendPath] = route.forwards!.split(' ') as [string, string]
      h.backend.on(method, backendPath, { status: 500, body: { detail: 'Internal Server Error' } })

      const status = await send(route.method, route.path, { json: route.json, form: route.form?.() })

      expect(h.backend.callsTo(method, backendPath).length, 'the request must reach the connector service').to.be.at.least(1)
      // The record stream relays the connector's own status; every other route maps an upstream 5xx to a 502.
      expect(status).to.equal(backendPath === STREAM_PATH ? 500 : 502)
      expect(visible().filter((r) => !REQUEST_LINES.has(r.message))).to.deep.equal([])
      expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
      // The record stream relays the connector's answer itself; every other route has one middleware line.
      const failureLines = backendPath === STREAM_PATH ? [] : [['warn', 'upstream_5xx']]
      expect(byMessage('http.request.error').map((r) => [level(r), r.error_class])).to.deep.equal(failureLines)
      expect(visible().map(pathIds)).to.deep.equal(visible().map(() => idsOf(route)))
    })
  }

  it('a knowledge base or folder name is never logged: a create or rename writes only the access line', async () => {
    h.backend.on('POST', '/api/v1/kb/', { status: 200, body: { id: KB_ID } })
    h.backend.on('PUT', `/api/v1/kb/${KB_ID}`, { status: 200, body: { id: KB_ID } })
    h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder`, { status: 201, body: { id: FOLDER_ID } })
    h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/subfolder`, { status: 201, body: { id: 'child' } })

    for (const [method, path, json, ids] of [
      ['POST', '/', { kbName: KB_NAME }, {}],
      ['PUT', `/${KB_ID}`, { kbName: KB_NAME }, { kb_id: KB_ID }],
      ['POST', `/${KB_ID}/folder`, { folderName: FOLDER_NAME }, { kb_id: KB_ID }],
      ['POST', `/${KB_ID}/folder/${FOLDER_ID}/subfolder`, { folderName: FOLDER_NAME }, { kb_id: KB_ID }],
    ] as const) {
      expect([200, 201], `${method} ${path}`).to.include(await send(method, path, { json }))
      expect(visible().map((r) => r.message), `${method} ${path}`).to.deep.equal(['http.request.end'])
      expect(visible().map(pathIds), `${method} ${path}`).to.deep.equal([ids])
      const text = JSON.stringify(mine())
      expect(text).to.not.include(KB_NAME)
      expect(text).to.not.include(FOLDER_NAME)
    }
  })

  it('a 404 for a knowledge base is the failure and access lines, each with kb_id, and no ERROR', async () => {
    h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 404, body: { detail: 'Knowledge base not found' } })

    expect(await send('GET', `/${KB_ID}`)).to.equal(404)

    expect(visible().filter((r) => r.message !== 'upstream.call.end').map((r) => [r.message, level(r), r.kb_id])).to.deep.equal([
      ['http.request.error', 'warn', KB_ID],
      ['http.request.end', 'info', KB_ID],
    ])
    expect(byMessage('http.request.error').map((r) => r.error_class)).to.deep.equal(['not_found'])
  })

  it('the connector service being down is no ERROR, and every line names the knowledge base', async () => {
    h.backend.on('GET', `/api/v1/kb/${KB_ID}/permissions`, 'drop')

    expect(await send('GET', `/${KB_ID}/permissions`)).to.equal(503)

    expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
    expect(visible().every((r) => r.kb_id === KB_ID)).to.equal(true)
    expect(byMessage('http.request.error').map((r) => r.error_class)).to.deep.equal(['unavailable'])
  })

  it('a refused path segment is never bound: 400 before the connector service, no id on any line', async () => {
    expect(await send('GET', '/..%2Fadmin')).to.equal(400)
    expect(await send('GET', '/record/..%2Fadmin')).to.equal(400)

    expect(h.backend.calls).to.deep.equal([])
    expect(mine().map(pathIds).filter((ids) => Object.keys(ids).length > 0)).to.deep.equal([])
  })

  describe('an upload', () => {
    beforeEach(() => {
      h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 200, body: { id: KB_ID, userRole: 'OWNER' } })
    })

    it('is one kb.upload.batch.end line with kb_id, and no line names the file', async () => {
      h.backend.on('POST', '/api/v1/document/internal/upload', { status: 200, body: { _id: 'doc-1', documentName: 'contract' } })
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/upload`, { status: 200, body: {} })

      expect(await send('POST', `/${KB_ID}/upload`, { form: pdfForm(FILE_NAME) })).to.equal(200)

      expect(visible().filter((r) => r.message !== 'upstream.call.end').map((r) => [r.message, level(r), r.kb_id, r.outcome])).to.deep.equal([
        ['kb.upload.batch.end', 'info', KB_ID, 'success'],
        ['http.request.end', 'info', KB_ID, 'success'],
      ])
      expect(JSON.stringify(knowledgeBaseLines())).to.not.include(FILE_NAME)
    })

    it("a file storage refuses is no ERROR and names no file; the client's stream says which file failed", async () => {
      h.backend.on('POST', '/api/v1/document/internal/upload', { status: 500, body: { error: { detail: 'disk full' } } })

      expect(await send('POST', `/${KB_ID}/upload`, { form: pdfForm(FILE_NAME) })).to.equal(200)

      expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
      expect(byMessage('Error creating placeholder document').map((r) => [level(r), r.status, r.error_class])).to.deep.equal([
        ['warn', 500, 'upstream_5xx'],
      ])
      expect(byMessage('Failed to create placeholder document for file').map((r) => [level(r), r.error_class])).to.deep.equal([
        ['debug', 'upstream_5xx'],
      ])
      expect(JSON.stringify(knowledgeBaseLines())).to.not.include(FILE_NAME)
    })

    /** The batch line as [level, outcome, files, succeeded, failed], and the client's `done` summary. */
    const batchEnd = (): { lines: unknown[][]; done: unknown } => ({
      lines: byMessage('kb.upload.batch.end').map((r) => [
        level(r),
        r.outcome,
        r.metadata?.files,
        r.metadata?.succeeded,
        r.metadata?.failed,
      ]),
      done: sseEvents(responseText).find((e) => e.event === 'done')?.data.summary,
    })

    it('one of two files refused at its placeholder: the batch line counts it, as the done summary does (partial)', async () => {
      let placeholders = 0
      h.backend.on('POST', '/api/v1/document/internal/upload', () =>
        (placeholders += 1) === 1
          ? { status: 500, body: { error: { detail: 'disk full' } } }
          : { status: 200, body: { _id: 'doc-2', documentName: 'second' } },
      )
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/upload`, { status: 200, body: {} })

      expect(await send('POST', `/${KB_ID}/upload`, { form: pdfForm(FILE_NAME, 'second.pdf') })).to.equal(200)

      expect(batchEnd()).to.deep.equal({
        lines: [['info', 'partial', 2, 1, 1]],
        done: { total: 2, succeeded: 1, failed: 1 },
      })
    })

    it('the indexing service unavailable: the batch line is WARN at its class, with its status', async () => {
      h.backend.on('POST', '/api/v1/document/internal/upload', { status: 200, body: { _id: 'doc-1', documentName: 'contract' } })
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/upload`, { status: 503, body: { detail: 'indexer down' } })

      expect(await send('POST', `/${KB_ID}/upload`, { form: pdfForm(FILE_NAME) })).to.equal(200)

      expect(batchEnd().lines).to.deep.equal([['warn', 'failure', 1, 0, 1]])
      expect(byMessage('kb.upload.batch.end').map((r) => [r.status, r.error_class])).to.deep.equal([[503, 'unavailable']])
    })

    it('every file refused at its placeholder: still one batch line, outcome=failure, and no indexing call', async () => {
      h.backend.on('POST', '/api/v1/document/internal/upload', { status: 500, body: { error: { detail: 'disk full' } } })

      expect(await send('POST', `/${KB_ID}/upload`, { form: pdfForm(FILE_NAME, 'second.pdf') })).to.equal(200)

      expect(h.backend.callsTo('POST', `/api/v1/kb/${KB_ID}/upload`)).to.deep.equal([])
      expect(batchEnd()).to.deep.equal({
        lines: [['info', 'failure', 2, 0, 2]],
        done: { total: 2, succeeded: 0, failed: 2 },
      })
      expect(byMessage('kb.upload.batch.end').map((r) => r.kb_id)).to.deep.equal([KB_ID])
    })
  })

  describe("the router's own failures go to the error middleware with no line of their own", () => {
    it('the upload processor failing is one ERROR, the middleware\'s', async () => {
      h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 200, body: { id: KB_ID, userRole: 'OWNER' } })
      sinon.stub(FileProcessorService.prototype, 'upload').throws(new TypeError('processor broke'))

      expect(await send('POST', `/${KB_ID}/upload`, { form: pdfForm(FILE_NAME) })).to.equal(500)

      expect(mine().filter((r) => level(r) === 'error').map((r) => [r.message, r.error_class])).to.deep.equal([
        ['http.request.error', 'internal'],
      ])
    })

    it("the limits answer failing is one ERROR, the middleware's", async () => {
      sinon.stub(KB_UPLOAD_LIMITS, 'maxFilesPerRequest').get(() => {
        throw new TypeError('limits unreadable')
      })

      expect(await send('GET', '/limits')).to.equal(500)

      expect(mine().filter((r) => level(r) === 'error').map((r) => [r.message, r.error_class])).to.deep.equal([
        ['http.request.error', 'internal'],
      ])
    })
  })

  it("a knowledge-hub search never logs the person's query text, at any level", async () => {
    h.backend.on('GET', '/api/v1/knowledge-hub/nodes', { status: 200, body: { items: [] } })

    expect(await send('GET', `/knowledge-hub/nodes?q=${encodeURIComponent(KB_NAME)}`)).to.equal(200)

    expect(h.backend.callsTo('GET', '/api/v1/knowledge-hub/nodes').length).to.equal(1)
    expect(JSON.stringify(mine())).to.not.include(KB_NAME)
  })

  describe('the record stream answers its own errors', () => {
    it('a status the connector service answered (404) is relayed with no line of its own', async () => {
      h.backend.on('GET', STREAM_PATH, { status: 404, body: { detail: 'Record not found' } })

      expect(await send('GET', `/stream/record/${RECORD_ID}`)).to.equal(404)

      expect(visible().filter((r) => !REQUEST_LINES.has(r.message))).to.deep.equal([])
      expect(byMessage('http.request.end').map((r) => [r.status, r.record_id])).to.deep.equal([[404, RECORD_ID]])
    })

    it('a conversion (?convertTo=pdf) writes no line of its own', async () => {
      h.backend.on('GET', STREAM_PATH, { status: 200, raw: 'pdf bytes' })

      expect(await send('GET', `/stream/record/${RECORD_ID}?convertTo=pdf`)).to.equal(200)

      expect(h.backend.callsTo('GET', STREAM_PATH).map((c) => c.query.get('convertTo'))).to.deep.equal(['pdf'])
      expect(visible().filter((r) => !REQUEST_LINES.has(r.message))).to.deep.equal([])
    })

    it('no answer from the connector service is 503, not 500, and no ERROR', async () => {
      h.backend.on('GET', STREAM_PATH, 'drop')

      expect(await send('GET', `/stream/record/${RECORD_ID}`)).to.equal(503)

      expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
      expect(byMessage('http.request.end').map((r) => [r.status, r.error_class, r.record_id])).to.deep.equal([
        [503, 'unavailable', RECORD_ID],
      ])
    })
  })
})
