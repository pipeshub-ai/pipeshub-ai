import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express, { Express, NextFunction, Request, Response } from 'express'
import sinon from 'sinon'
import winston from 'winston'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { createGlobalRateLimiter, createKeyedRateLimiter } from '../../../src/libs/middlewares/rate-limit.middleware'
import { xssSanitizationMiddleware } from '../../../src/libs/middlewares/xss-sanitization.middleware'
import { rejectClientConversationHistory } from '../../../src/modules/enterprise_search/utils/client-history-guard'
import { Logger } from '../../../src/libs/services/logger.service'
import { LogRecord, captureLogRecords, newRequestId, recordsOf } from '../../helpers/log-capture'

// The global limiter and the XSS middleware run before routing, so there is no route template: their lines carry
// the path with each segment that is not an id masked (the rule `params` uses), ids kept. Neither redactor would
// remove an email or a name a caller put in the URL.

const EMAIL = 'fake-ada@example.com'
const NAME = 'Fake Ada Lovelace'
const ID = '64f0c0ffee00000000000a5a'
const MASK = '[not an id]'
const MESSAGE = Symbol.for('message')

type Answer = { status: number; lines: LogRecord[] }

describe('middleware WARN/ERROR lines never log a path segment that is not an id', () => {
  let records: LogRecord[]
  const servers: http.Server[] = []

  beforeEach(() => {
    records = captureLogRecords()
  })

  afterEach(async () => {
    sinon.restore()
    delete process.env.LOG_FORMAT
    Logger.applyEnv()
    await Promise.all(servers.splice(0).map((s) => new Promise<void>((resolve) => s.close(() => resolve()))))
  })

  /** The production front of the app: request id, access line, body parser. */
  const front = (): Express => {
    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    return app
  }

  const listen = async (app: Express): Promise<number> => {
    app.use(ErrorMiddleware.handleError())
    const server = http.createServer((req, res) => void app(req, res))
    servers.push(server)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    return (server.address() as AddressInfo).port
  }

  const send = async (port: number, method: string, path: string, body?: unknown): Promise<Answer> => {
    const requestId = newRequestId('rawpath')
    const payload = body === undefined ? undefined : JSON.stringify(body)
    const status = await new Promise<number>((resolve, reject) => {
      const headers: Record<string, string> = { 'x-request-id': requestId }
      if (payload !== undefined) headers['content-type'] = 'application/json'
      const req = http.request({ host: '127.0.0.1', port, path, method, headers }, (res) => {
        res.resume()
        res.on('end', () => resolve(res.statusCode ?? 0))
      })
      req.on('error', reject)
      req.end(payload)
    })
    const deadline = Date.now() + 5000
    while (Date.now() < deadline && !recordsOf(records, requestId).some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setImmediate(resolve))
    }
    return { status, lines: recordsOf(records, requestId) }
  }

  const root = () => (Logger.getInstance() as unknown as { logger: winston.Logger }).logger
  /** What an operator reads: the record through the root format and the console transport. */
  const rendered = (record: LogRecord, format: 'text' | 'json'): string => {
    process.env.LOG_FORMAT = format
    Logger.applyEnv()
    const console = root().transports.find((t) => t instanceof winston.transports.Console)!
    const shaped = root().format.transform({ ...record }, {}) as Record<string | symbol, unknown>
    return String((console.format!.transform(shaped, {}) as Record<symbol, unknown>)[MESSAGE])
  }
  /** The lines (text or JSON output) that carry `value`, raw or percent-encoded. */
  const carriers = (lines: LogRecord[], value: string): string[] =>
    lines.flatMap((line) =>
      (['text', 'json'] as const)
        .filter((format) => [value, encodeURIComponent(value)].some((v) => rendered(line, format).includes(v)))
        .map((format) => `${String(line.message)} ${format}: ${rendered(line, 'json')}`),
    )
  const line = (r: Answer, message: string): LogRecord => {
    const found = r.lines.find((l) => l.message === message)
    expect(found, `${message} in ${JSON.stringify(r.lines.map((l) => l.message))}`).to.not.equal(undefined)
    return found!
  }
  const pathOf = (record: LogRecord): unknown => record.metadata?.path ?? record.path

  it('1. global limiter (before routing): the 429 WARN masks an email segment and keeps the rest', async () => {
    const app = front()
    app.use(createGlobalRateLimiter(Logger.getInstance({ service: 'RateLimit' }), 1))
    app.get('/api/v1/users/:id/role', (_req, res) => res.json({}))
    const port = await listen(app)
    await send(port, 'GET', `/api/v1/users/${EMAIL}/role`)
    const r = await send(port, 'GET', `/api/v1/users/${EMAIL}/role`)
    expect(r.status).to.equal(429)
    expect(carriers(r.lines, EMAIL)).to.deep.equal([])
    expect(pathOf(line(r, 'Rate limit exceeded'))).to.equal(`/api/v1/users/${MASK}/role`)
  })

  it('2. global limiter: an id segment is kept as sent (support names the entity), the query is not logged', async () => {
    const app = front()
    app.use(createGlobalRateLimiter(Logger.getInstance({ service: 'RateLimit' }), 1))
    app.get('/api/v1/kb/:kbId/records', (_req, res) => res.json({}))
    const port = await listen(app)
    // A query is never logged: a "/" in it would make words after it look like id segments.
    const target = `/api/v1/kb/${ID}/records?next=/x/Ada.Lovelace`
    await send(port, 'GET', target)
    const r = await send(port, 'GET', target)
    expect(r.status).to.equal(429)
    expect(pathOf(line(r, 'Rate limit exceeded'))).to.equal(`/api/v1/kb/${ID}/records`)
  })

  it('3. keyed limiter on a route: full path (mount included), a percent-encoded name masked', async () => {
    const app = front()
    const skills = express.Router()
    const limiter = createKeyedRateLimiter(Logger.getInstance({ service: 'RateLimit' }), {
      prefix: 'rawpath',
      maxRequestsPerMinute: 1,
      message: 'Too many.',
    })
    skills.get('/:name', limiter, (_req, res) => res.json({}))
    app.use('/api/v1/skills', skills)
    const port = await listen(app)
    await send(port, 'GET', `/api/v1/skills/${encodeURIComponent(NAME)}`)
    const r = await send(port, 'GET', `/api/v1/skills/${encodeURIComponent(NAME)}`)
    expect(r.status).to.equal(429)
    expect(carriers(r.lines, 'Lovelace')).to.deep.equal([])
    expect(pathOf(line(r, 'Rate limit exceeded'))).to.equal(`/api/v1/skills/${MASK}`)
  })

  it('4. client-history guard: no path on its WARN (the error line has the route), no line holds the value', async () => {
    const app = front()
    app.post('/api/v1/conversations/:conversationId/messages', rejectClientConversationHistory, (_req, res) => res.json({}))
    const port = await listen(app)
    const r = await send(port, 'POST', `/api/v1/conversations/${EMAIL}/messages`, {
      query: 'hi',
      previousConversations: [{ role: 'user', content: 'x' }],
    })
    expect(r.status).to.equal(400)
    expect(carriers(r.lines, EMAIL)).to.deep.equal([])
    // The request's own http.request.error line names the route, so the guard's line needs no path.
    expect(pathOf(line(r, 'Rejected client-supplied conversation history'))).to.equal(undefined)
    expect(line(r, 'http.request.error').route).to.equal('/api/v1/conversations/:conversationId/messages')
  })

  it('5. XSS middleware failure (before routing): the ERROR masks a non-id segment', async () => {
    const app = front()
    // A sanitizer bug stand-in: reading the body throws something other than its own BadRequestError.
    app.use((req: Request, _res: Response, next: NextFunction) => {
      req.body = Object.defineProperty({}, 'x', {
        enumerable: true,
        get: () => {
          throw new Error('sanitizer bug')
        },
      })
      next()
    })
    app.use(xssSanitizationMiddleware)
    app.get('/api/v1/users/:id', (_req, res) => res.json({}))
    const port = await listen(app)
    const r = await send(port, 'GET', `/api/v1/users/${EMAIL}`)
    expect(r.status).to.equal(200)
    expect(carriers(r.lines, EMAIL)).to.deep.equal([])
    expect(pathOf(line(r, 'XSS sanitization middleware error'))).to.equal(`/api/v1/users/${MASK}`)
  })
})
