import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import sinon from 'sinon'
import winston from 'winston'
import { z } from 'zod'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { bindPathLogFields, guardPathParams } from '../../../src/libs/middlewares/safe-path-params.middleware'
import { InternalServerError, NotFoundError } from '../../../src/libs/errors/http.errors'
import { Logger } from '../../../src/libs/services/logger.service'
import { LogRecord, captureLogRecords, newRequestId, recordsOf } from '../../helpers/log-capture'

// The error middleware's `http.request.error` line carries the route's params (`metadata.params`), and the bound
// path fields are on every line of the request. Neither redactor removes an email, a name or a file name, so a value
// the caller put in a path segment is kept only when it is an id. Support needs the route template (a field), the
// param names and the ids, never a value that is not an id.

const EMAIL = 'n5n-fake-ada@example.com'
const FILE_PATH = 'reports/N5N Fake Ada Lovelace CV.pdf'
const ID = '64f0c0ffee00000000000a5a'
const MESSAGE = Symbol.for('message')
const objectId = z.string().regex(/^[0-9a-f]{24}$/)

describe('ErrorMiddleware: http.request.error never logs a route-param value that is not an id', () => {
  let server: http.Server
  let port = 0
  let records: LogRecord[]
  let middlewareLogger: unknown

  before(async () => {
    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    // A schema-checked id (most routes).
    app.get(
      '/api/v1/things/:thingId',
      ValidationMiddleware.validate(z.object({ params: z.object({ thingId: objectId }) })),
      () => {
        throw new InternalServerError('thing broke')
      },
    )
    // A query-checked list.
    app.get('/api/v1/things', ValidationMiddleware.validate(z.object({ query: z.object({ owner: objectId }) })), () => {
      throw new InternalServerError('unreachable')
    })
    // An id the controller looks up without a schema (users.routes `/:id` style): unknown, so 404.
    app.get('/api/v1/users/:id', () => {
      throw new NotFoundError('User not found')
    })
    // A wildcard file route (storage.routes `/internal/artifacts/:documentId/files/*`): params[0] is the path.
    app.get('/api/v1/artifacts/:documentId/files/*', () => {
      throw new NotFoundError('File not found')
    })
    // A name the schema accepts (skills.routes `/:name`, openai_compat `/models/*`): the request succeeds.
    app.get(
      '/api/v1/skills/:name',
      ValidationMiddleware.validate(z.object({ params: z.object({ name: z.string().min(1) }) })),
      (_req, res) => res.json({}),
    )
    // A router that guards and binds its ids (connectors, kb, toolsets, mcp servers).
    const connectors = express.Router()
    guardPathParams(connectors, 'connectorId')
    bindPathLogFields(connectors, { connectorId: 'connector_id', connectorType: 'connector_type' })
    connectors.get('/types/:connectorType', (_req, res) => res.json({}))
    connectors.get(
      '/:connectorId',
      ValidationMiddleware.validate(z.object({ params: z.object({ connectorId: z.uuid() }) })),
      (_req, res) => res.json({}),
    )
    app.use('/api/v1/connectors', connectors)
    app.use(ErrorMiddleware.handleError())
    server = http.createServer((req, res) => void app(req, res))
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    port = (server.address() as AddressInfo).port
  })

  after(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  beforeEach(() => {
    // Another file may have replaced the root since ErrorMiddleware took its logger at load.
    middlewareLogger = (ErrorMiddleware as any).logger
    ;(ErrorMiddleware as any).logger = Logger.getInstance({ service: 'ErrorMiddleware' })
    records = captureLogRecords()
  })

  afterEach(() => {
    ;(ErrorMiddleware as any).logger = middlewareLogger
    sinon.restore()
    delete process.env.LOG_FORMAT
    Logger.applyEnv()
  })

  const root = () => (Logger.getInstance() as unknown as { logger: winston.Logger }).logger
  /** What an operator reads: the record through the root format and the console transport, text or JSON. */
  const rendered = (record: LogRecord, format: 'text' | 'json'): string => {
    process.env.LOG_FORMAT = format
    Logger.applyEnv()
    const console = root().transports.find((t) => t instanceof winston.transports.Console)!
    const shaped = root().format.transform({ ...record }, {}) as Record<string | symbol, unknown>
    return String((console.format!.transform(shaped, {}) as Record<symbol, unknown>)[MESSAGE])
  }

  const get = async (path: string) => {
    const requestId = newRequestId('n5n')
    const answer = await new Promise<{ status: number; text: string }>((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path, method: 'GET', headers: { 'x-request-id': requestId } }, (res) => {
        let text = ''
        res.setEncoding('utf8')
        res.on('data', (chunk: string) => (text += chunk))
        res.on('end', () => resolve({ status: res.statusCode ?? 0, text }))
      })
      req.on('error', reject)
      req.end()
    })
    const deadline = Date.now() + 5000
    while (Date.now() < deadline && !recordsOf(records, requestId).some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setImmediate(resolve))
    }
    const lines = recordsOf(records, requestId)
    const errorLine = lines.find((r) => r.message === 'http.request.error')
    return { ...answer, lines, errorLine }
  }

  /** The lines (text and JSON) that carry `value`. */
  const carriers = (lines: LogRecord[], value: string): string[] => {
    expect(lines.map((l) => l.message)).to.include('http.request.end')
    return lines
      .flatMap((line) =>
        (['text', 'json'] as const)
          .filter((format) => [value, encodeURIComponent(value)].some((v) => rendered(line, format).includes(v)))
          .map((format) => `${String(line.message)} ${format}: ${rendered(line, 'json')}`),
      )
  }
  const expectNowhere = (lines: LogRecord[], value: string) => expect(carriers(lines, value)).to.deep.equal([])

  it('a param the schema refuses: 400, its name on the line, not its value', async () => {
    const r = await get(`/api/v1/things/${encodeURIComponent(EMAIL)}`)
    expect(r.status).to.equal(400)
    expect(r.errorLine).to.include({ level: 'warning', route: '/api/v1/things/:thingId', error_class: 'bad_request' })
    expect(r.errorLine!.metadata.error.metadata.errors[0].field).to.equal('params.thingId')
    expect(r.errorLine!.metadata.params).to.deep.equal({ thingId: '[not an id]' })
    expectNowhere(r.lines, EMAIL)
  })

  it('an id no schema checks, refused by the controller (404): not logged', async () => {
    const r = await get(`/api/v1/users/${encodeURIComponent(EMAIL)}`)
    expect(r.status).to.equal(404)
    expect(r.errorLine!.metadata.params).to.have.all.keys('id')
    expectNowhere(r.lines, EMAIL)
  })

  it('a wildcard file path (a file name can hold a person\'s name): not logged, the document id is', async () => {
    const r = await get(`/api/v1/artifacts/${ID}/files/${FILE_PATH.split('/').map(encodeURIComponent).join('/')}`)
    expect(r.status).to.equal(404)
    expect(r.errorLine!.metadata.params.documentId).to.equal(ID)
    expectNowhere(r.lines, 'Ada Lovelace')
  })

  it('keeps an id: a failure on a valid id still names the entity (support)', async () => {
    const r = await get(`/api/v1/things/${ID}`)
    expect(r.status).to.equal(500)
    expect(r.errorLine).to.include({ level: 'error', error_class: 'internal' })
    expect(r.errorLine!.metadata.params).to.deep.equal({ thingId: ID })
    expect(rendered(r.errorLine!, 'json')).to.include(ID)
  })

  it('a refused query value was never logged (pin)', async () => {
    const r = await get(`/api/v1/things?owner=${encodeURIComponent(EMAIL)}`)
    expect(r.status).to.equal(400)
    expect(r.errorLine!.metadata.error.metadata.errors[0].field).to.equal('query.owner')
    expectNowhere(r.lines, EMAIL)
  })

  it('the dev-mode 400 body is unchanged: the field names the param, no params or value (client contract)', async () => {
    const saved = process.env.NODE_ENV
    process.env.NODE_ENV = 'development'
    try {
      const r = await get(`/api/v1/things/${encodeURIComponent(EMAIL)}`)
      const body = JSON.parse(r.text)
      expect(body.error.code).to.equal('VALIDATION_ERROR')
      expect(body.error.metadata.errors).to.have.length(1)
      expect(body.error.metadata.errors[0]).to.include({ field: 'params.thingId', value: '' })
      expect(body.error.metadata).to.not.have.property('params')
      expect(r.text).to.not.include(EMAIL)
    } finally {
      if (saved === undefined) delete process.env.NODE_ENV
      else process.env.NODE_ENV = saved
    }
  })

  it('an accepted request writes no param value either (DEBUG `Validation successful` logged req.path)', async () => {
    // `@` is legal unencoded in a path segment, so req.path holds the address as sent.
    const r = await get(`/api/v1/skills/${EMAIL}`)
    expect(r.status).to.equal(200)
    expect(r.errorLine).to.equal(undefined)
    expectNowhere(r.lines, EMAIL)
  })

  describe('a router that binds its ids to log fields', () => {
    it('a refused connector id is not on any line of the request, bound field included', async () => {
      const r = await get(`/api/v1/connectors/${encodeURIComponent(EMAIL)}`)
      expect(r.status).to.equal(400)
      expectNowhere(r.lines, EMAIL)
    })

    it('a valid connector id is still bound on the access line', async () => {
      const id = '0b9a3c3e-6f3d-4a51-9c2e-5a1b7d2e8f10'
      const r = await get(`/api/v1/connectors/${id}`)
      expect(r.status).to.equal(200)
      expect(r.lines.find((l) => l.message === 'http.request.end')).to.include({ connector_id: id })
    })

    it('a connector type is a name, not an id: still bound with its space', async () => {
      const r = await get('/api/v1/connectors/types/SHAREPOINT%20ONLINE')
      expect(r.status).to.equal(200)
      expect(r.lines.find((l) => l.message === 'http.request.end')).to.include({ connector_type: 'SHAREPOINT ONLINE' })
    })

    it('an email as a connector type is not a name: not bound, on no line', async () => {
      const r = await get(`/api/v1/connectors/types/${EMAIL}`)
      expect(r.status).to.equal(200)
      expect(r.lines.find((l) => l.message === 'http.request.end')).to.not.have.property('connector_type')
      expectNowhere(r.lines, EMAIL)
    })
  })
})
