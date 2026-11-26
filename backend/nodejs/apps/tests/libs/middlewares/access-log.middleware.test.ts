import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express, { Request } from 'express'
import http from 'node:http'
import { EventEmitter } from 'node:events'
import { AddressInfo } from 'node:net'
import { accessLog, pathForLog, routeParams } from '../../../src/libs/middlewares/access-log.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { Logger } from '../../../src/libs/services/logger.service'
import * as seam from '../../../src/libs/services/structured-logging'
import { runWithRequestContext, setContextIdentity } from '../../../src/libs/context/request-context'
import { UnauthorizedError } from '../../../src/libs/errors/http.errors'

const MESSAGE = Symbol.for('message')
const ORG = '64f0c0ffee0000000000000a'
const USER = '64f0c0ffee0000000000000b'
const SECRETS = ['QA-FAKE-CODE-1', 'QA-FAKE-CODE-2', 'qa-agent/9.9']

describe('accessLog: one http.request.end line per request', () => {
  let records: Record<string, any>[]
  let waiters: (() => void)[]
  let server: http.Server
  let base: string
  let middlewareLogger: unknown
  let release: (() => void) | undefined
  let arrived: Promise<void>

  /** Resolves once `n` access lines have been written (the line is written on `close`, after the client has its answer). */
  const accessLines = async (n: number): Promise<Record<string, any>[]> => {
    const lines = () => records.filter((r) => r.message === 'http.request.end')
    while (lines().length < n) await new Promise<void>((resolve) => waiters.push(resolve))
    return lines()
  }
  const errorLines = () => records.filter((r) => r.message === 'http.request.error')
  const text = (r: Record<string, unknown>): string =>
    String(((Logger.getInstance() as any).logger.format.transform({ ...r }, {}) as Record<symbol, unknown>)[MESSAGE])

  const request = (path: string, options: http.RequestOptions = {}, body?: string) =>
    new Promise<number>((resolve, reject) => {
      const req = http.request(`${base}${path}`, { agent: false, ...options }, (res) => {
        res.resume()
        res.on('end', () => resolve(res.statusCode ?? 0))
      })
      req.on('error', reject)
      req.end(body)
    })

  const buildApp = (): express.Express => {
    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    // Stands in for the auth middleware: the verified identity lands in the store in place.
    app.use((req, _res, next) => {
      if (req.get('authorization')) setContextIdentity({ orgId: ORG, userId: USER })
      next()
    })
    const records = express.Router()
    records.get('/:kbId/records/:rid', (_req, res) => {
      res.json({ ok: true })
    })
    records.get('/:kbId/fail/:rid', async (_req, _res, next) => {
      await Promise.resolve()
      next(new TypeError("Cannot read properties of undefined (reading 'x')"))
    })
    records.get('/:kbId/throw/:rid', () => {
      throw new TypeError('sync bug')
    })
    records.get('/:kbId/down', () => {
      throw Object.assign(new TypeError('fetch failed'), {
        cause: Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8000'), { code: 'ECONNREFUSED' }),
      })
    })
    records.post('/:kbId/upload', (_req, res) => {
      res.json({ ok: true })
    })
    records.get('/:kbId/slow', async (_req, res) => {
      release!()
      await new Promise(() => undefined)
      res.end()
    })
    records.get('/:kbId/stream', (_req, res, next) => {
      res.write('partial')
      next(new Error('stream broke'))
    })
    app.use('/api/v1/kb', records)
    const guarded = express.Router()
    guarded.use((_req, _res, next) => next(new UnauthorizedError('Invalid token')))
    guarded.get('/tokens', (_req, res) => {
      res.json({})
    })
    app.use('/api/v1/pat', guarded)
    app.get(/^\/api\/v1\/regex\/.*/, (_req, res) => {
      res.json({})
    })
    app.get('/api/v1/document/internal/records/:id', (_req, res) => {
      res.json({})
    })
    app.get('/api/v1/health', (_req, res) => {
      res.json({ status: 'healthy' })
    })
    app.get('/page', (_req, res) => {
      res.send('<html></html>')
    })
    // shortcut pinned below: a parameterised mount renders its raw value.
    const project = express.Router({ mergeParams: true })
    project.get('/items', (_req, res) => {
      res.json({})
    })
    app.use('/api/v1/projects/:projectId', project)
    app.use(ErrorMiddleware.handleError())
    return app
  }

  beforeEach(async () => {
    ;(Logger as any).instance = null
    sinon.stub(seam, 'getProvider').returns(
      Object.assign(new seam.DefaultLoggingProvider(), { fileLoggingEnabled: () => false }),
    )
    records = []
    waiters = []
    const root = Logger.getInstance()
    ;(root as any).logger.level = 'debug'
    sinon.stub((root as any).logger, 'log').callsFake((r: any) => {
      records.push(r)
      for (const wake of waiters.splice(0)) wake()
    })
    middlewareLogger = (ErrorMiddleware as any).logger
    ;(ErrorMiddleware as any).logger = Logger.getInstance({ service: 'ErrorMiddleware' })
    arrived = new Promise<void>((resolve) => {
      release = resolve
    })
    const app = buildApp()
    server = http.createServer((req, res) => {
      void app(req, res)
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  afterEach(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    ;(ErrorMiddleware as any).logger = middlewareLogger
    sinon.restore()
    ;(Logger as any).instance = null
  })

  it('says what, who, how it ended and how long it took, with the route template', async () => {
    const status = await request('/api/v1/kb/kb1/records/r1?code=QA-FAKE-CODE-1', {
      headers: {
        authorization: 'Bearer x',
        'x-request-id': 'req-abc',
        referer: 'https://app/cb?code=QA-FAKE-CODE-2',
        'user-agent': 'qa-agent/9.9',
      },
    })
    expect(status).to.equal(200)
    const line = (await accessLines(1))[0]!
    expect(line).to.include({
      level: 'info', service: 'HttpAccess', method: 'GET', route: '/api/v1/kb/:kbId/records/:rid', status: 200,
      outcome: 'success', request_id: 'req-abc', org_id: ORG, user_id: USER,
    })
    expect(line.duration_ms).to.be.a('number').and.satisfy(Number.isInteger).and.at.least(0)
    expect(line.client_ip).to.be.a('string').and.include('127.0.0.1')
    expect(line).to.not.have.property('error_class')
    const rendered = text(line)
    expect(rendered).to.include('http.request.end | org_id=')
    for (const secret of SECRETS) {
      expect(JSON.stringify(line), secret).to.not.include(secret)
      expect(rendered, secret).to.not.include(secret)
    }
  })

  it('keeps the full template after next(err) and after a sync throw; our 500 is one ERROR line, the access line a warning', async () => {
    expect(await request('/api/v1/kb/kb1/fail/r2')).to.equal(500)
    expect(await request('/api/v1/kb/kb1/throw/r3')).to.equal(500)
    const lines = await accessLines(2)
    expect(lines.map((l) => [l.route, l.level, l.status, l.error_class, l.outcome])).to.deep.equal([
      ['/api/v1/kb/:kbId/fail/:rid', 'warning', 500, 'internal', 'failure'],
      ['/api/v1/kb/:kbId/throw/:rid', 'warning', 500, 'internal', 'failure'],
    ])
    const errors = records.filter((r) => r.level === 'error')
    expect(errors).to.have.length(2)
    expect(errors.every((r) => r.message === 'http.request.error')).to.equal(true)
    expect(errors[0]).to.include({ route: '/api/v1/kb/:kbId/fail/:rid', error_class: 'internal', error_type: 'TypeError' })
    expect(errors[0]!.metadata.params).to.deep.equal({ kbId: 'kb1', rid: 'r2' })
    expect(errors[0]!.metadata.error.stack).to.be.a('string')
  })

  it('an unreachable service is a 503 with no ERROR line at all', async () => {
    expect(await request('/api/v1/kb/kb1/down')).to.equal(503)
    const [line] = await accessLines(1)
    expect(line).to.include({ level: 'warning', status: 503, error_class: 'unavailable' })
    expect(records.filter((r) => r.level === 'error')).to.have.length(0)
    expect(errorLines()[0]).to.include({ level: 'warning', error_class: 'unavailable' })
    expect(errorLines()[0]!.metadata.error.cause).to.include({ name: 'TypeError', message: 'fetch failed' })
  })

  it('a router that refuses before any route matched gives its mount', async () => {
    expect(await request('/api/v1/pat/tokens')).to.equal(401)
    const [line] = await accessLines(1)
    expect(line).to.include({ level: 'info', route: '/api/v1/pat', status: 401, error_class: 'auth_expired' })
    expect(errorLines()[0]).to.include({ level: 'warning', route: '/api/v1/pat' })
  })

  it('a malformed body is logged as a 400 without the body', async () => {
    const status = await request('/api/v1/kb/kb1/upload', { method: 'POST', headers: { 'content-type': 'application/json' } }, '{"password":"QA-FAKE-CODE-1",')
    expect(status).to.equal(400)
    const [line] = await accessLines(1)
    // The parser's error passes the router's mount on its way to the error middleware.
    expect(line).to.include({ status: 400, error_class: 'bad_request', route: '/api/v1/kb' })
    expect(JSON.stringify(records)).to.not.include('QA-FAKE-CODE-1')
  })

  it('nothing matched is unmatched; a RegExp route is unmatched; a parameterised mount shows its raw value', async () => {
    await request('/api/v1/nothing/here')
    await request('/api/v1/regex/abc')
    await request('/api/v1/projects/p42/items')
    const lines = await accessLines(3)
    expect(lines.map((l) => [l.route, l.status])).to.deep.equal([
      ['unmatched', 404],
      ['unmatched', 200],
      ['/api/v1/projects/p42/items', 200],
    ])
    expect(lines[0]).to.include({ error_class: 'not_found', level: 'info' })
  })

  it('pages get no line; internal calls and health polls are debug', async () => {
    await request('/page')
    await request('/api/v1/document/internal/records/x')
    await request('/api/v1/health')
    const lines = await accessLines(2)
    expect(lines.map((l) => [l.route, l.level])).to.deep.equal([
      ['/api/v1/document/internal/records/:id', 'debug'],
      ['/api/v1/health', 'debug'],
    ])
  })

  it('a client that goes away is cancelled at INFO, with no status when nothing was sent', async () => {
    const req = http.get(`${base}/api/v1/kb/kb1/slow`, { agent: false })
    req.on('error', () => undefined)
    await arrived
    req.destroy()
    const [line] = await accessLines(1)
    expect(line).to.include({ level: 'info', error_class: 'cancelled', outcome: 'failure', route: '/api/v1/kb/:kbId/slow' })
    expect(line).to.not.have.property('status')
  })

  it('a stream that fails after its headers went out is logged once, and the access line shares the class', async () => {
    const req = http.get(`${base}/api/v1/kb/kb1/stream`, { agent: false }, (res) => {
      res.once('data', () => req.destroy())
    })
    req.on('error', () => undefined)
    const [line] = await accessLines(1)
    expect(errorLines()).to.have.length(1)
    expect(errorLines()[0]).to.include({ level: 'error', status: 200, error_class: 'internal' })
    expect(line).to.include({ error_class: 'internal', status: 200 })
  })

  it('writes exactly one line per request on a kept-alive connection', async () => {
    const agent = new http.Agent({ keepAlive: true, maxSockets: 1 })
    try {
      await request('/api/v1/kb/kb1/records/a', { agent, headers: { 'x-request-id': 'req-a' } })
      await request('/api/v1/kb/kb1/records/b', { agent, headers: { 'x-request-id': 'req-b' } })
      await accessLines(2)
      // A third request on the same socket: its line can only arrive after any duplicate of the first two.
      await request('/api/v1/kb/kb1/records/c', { agent, headers: { 'x-request-id': 'req-c' } })
      const lines = await accessLines(3)
      expect(lines.map((l) => l.request_id)).to.deep.equal(['req-a', 'req-b', 'req-c'])
    } finally {
      agent.destroy()
    }
  })

  it('a logger that throws inside the close listener does not become an uncaught exception', () => {
    const consoleError = sinon.stub(console, 'error')
    sinon.stub(Logger.prototype, 'info').throws(new Error('provider hook broke'))
    const req: any = { method: 'GET', originalUrl: '/api/v1/x', baseUrl: '', params: {}, ip: '10.0.0.1' }
    const res: any = Object.assign(new EventEmitter(), { headersSent: true, statusCode: 200, writableFinished: true })
    void accessLog()(req, res, () => undefined)
    expect(() => res.emit('close')).to.not.throw()
    expect(consoleError.calledWithMatch('Logging the access line failed:')).to.be.true
  })

  it('logs with the request context even when close fires outside it', () => {
    const req: any = { method: 'GET', originalUrl: '/api/v1/x', baseUrl: '', params: {}, ip: '10.0.0.1' }
    const res: any = Object.assign(new EventEmitter(), { headersSent: true, statusCode: 204, writableFinished: true })
    runWithRequestContext({ rootId: 'req-outside' }, () => {
      setContextIdentity({ orgId: ORG, userId: USER })
      void accessLog()(req, res, () => undefined)
    })
    res.emit('close')
    expect(records[0]).to.include({ request_id: 'req-outside', org_id: ORG, status: 204, client_ip: '10.0.0.1', route: 'unmatched' })
  })
})

describe('routeParams: an id as sent, any other value by its name only', () => {
  const shown = (value: string) => routeParams({ params: { p: value } } as unknown as Request).p
  const label = (value: string) => JSON.stringify(value.length > 40 ? `${value.slice(0, 8)}… (${value.length} chars)` : value)

  for (const value of ['Ada Lovelace', 'a'.repeat(129), '100%', 'a/b', 'n5n-fake-ada@example.com']) {
    it(`masks ${label(value)}`, () => {
      expect(shown(value)).to.equal('[not an id]')
    })
  }

  for (const value of [ORG, '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90', '0123456789abcdef0123456789abcdef', 'org:key.v1', 'a'.repeat(128)]) {
    it(`keeps ${label(value)}`, () => {
      expect(shown(value)).to.equal(value)
    })
  }
})

describe('pathForLog: the request path a log line may carry', () => {
  const OBJECT_ID = '64f0c0ffee0000000000000a'
  const UUID = '0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0'
  const at = (path: string, baseUrl = '') => pathForLog({ baseUrl, path } as express.Request)
  const id128 = 'a'.repeat(128)

  const cases: [string, string, string, string][] = [
    ['the root', '', '/', '/'],
    ['ids and words', '', `/api/v1/kb/${OBJECT_ID}/records`, `/api/v1/kb/${OBJECT_ID}/records`],
    ['a uuid and an all_ id', '', `/api/v1/x/${UUID}/all_${OBJECT_ID}`, `/api/v1/x/${UUID}/all_${OBJECT_ID}`],
    ['a trailing slash', '', '/api/v1/users/', '/api/v1/users/'],
    ['an email', '', '/api/v1/users/a@b.c/role', '/api/v1/users/[not an id]/role'],
    ['a percent-encoded name', '', '/api/v1/skills/Ada%20L', '/api/v1/skills/[not an id]'],
    ['a 128-char id', '', `/x/${id128}`, `/x/${id128}`],
    ['a 129-char segment', '', `/x/${id128}a`, '/x/[not an id]'],
    ['the mount, inside a router', '/api/v1/skills', '/x@y', '/api/v1/skills/[not an id]'],
    ['the mount itself (Express gives path "/")', '/api/v1/skills', '/', '/api/v1/skills/'],
  ]
  for (const [name, baseUrl, path, expected] of cases) {
    it(`${name}: ${baseUrl}${path} -> ${expected}`, () => {
      expect(at(path, baseUrl)).to.equal(expected)
    })
  }

  it('keeps a 256-char path whole and cuts a 257-char one after its last whole segment', () => {
    const path256 = `/${id128}/${'b'.repeat(126)}`
    expect(path256).to.have.length(256)
    expect(at(path256)).to.equal(path256)
    expect(at(`${path256}c`)).to.equal(`/${id128}/…`)
  })

  it('stays within 256 chars, "…" included, when a "/" sits at index 255', () => {
    const slashAt255 = `/${id128}/${'b'.repeat(125)}/c`
    expect(slashAt255.indexOf('/', 130)).to.equal(255)
    expect(at(slashAt255)).to.equal(`/${id128}/…`)
    expect(at('/'.repeat(257))).to.equal(`${'/'.repeat(255)}…`)
  })

  it('cuts a path the mask made longer (every "@" becomes the marker) at a "/"', () => {
    const shown = at('/@'.repeat(8000))
    expect(shown).to.equal(`${'/[not an id]'.repeat(21)}/…`)
    expect(shown).to.have.length(254)
  })

  it('cuts by the masked length, not the raw one', () => {
    expect('/@'.repeat(30)).to.have.length(60)
    expect(at('/@'.repeat(30))).to.equal(`${'/[not an id]'.repeat(21)}/…`)
  })

  it('never splits a segment, so a JWT across the cut is dropped, not left half-redactable', () => {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJuNW5iLWZha2UifQ.bjVuYi1mYWtlLXNpZw'
    const prefix = `/${id128}/${'b'.repeat(100)}`
    expect(prefix.length).to.be.below(256)
    expect(prefix.length + 1 + jwt.length).to.be.above(256)
    const shown = at(`${prefix}/${jwt}`)
    expect(shown).to.equal(`${prefix}/…`)
    expect(shown).not.to.include('eyJ')
  })
})
