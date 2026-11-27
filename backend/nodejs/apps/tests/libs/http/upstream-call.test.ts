import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import http from 'node:http'
import { AddressInfo } from 'node:net'
import winston from 'winston'
import axios, { AxiosAdapter, AxiosError, InternalAxiosRequestConfig } from 'axios'
import axiosRetry from 'axios-retry'
import { Logger } from '../../../src/libs/services/logger.service'
import * as seam from '../../../src/libs/services/structured-logging'
import { runWithRequestContext, setContextIdentity } from '../../../src/libs/context/request-context'
import { InternalServerError } from '../../../src/libs/errors/http.errors'
import {
  checkUpstreamUrl,
  endUpstreamCall,
  installUpstreamLogging,
  isUpstreamRefusal,
  registerUpstreams,
  upstreamFetch,
  upstreamTarget,
} from '../../../src/libs/http/upstream-call'
import { RETRY_POLICY } from '../../../src/libs/http/retry-policy'

const MESSAGE = Symbol.for('message')
const ORG = '64f0c0ffee0000000000000a'
const USER = '64f0c0ffee0000000000000b'
const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNlY3JldA'
const BODY_SECRET = 'alice@example.com'

const refusedFetch = (): TypeError =>
  new TypeError('fetch failed', {
    cause: Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8000'), { code: 'ECONNREFUSED' }),
  })

describe('upstream-call: one upstream.call.end per outbound attempt', () => {
  let records: Record<string, any>[]
  let server: http.Server
  let origin: string
  let received: Array<{ path: string; requestId: string | undefined }>

  const freshLogger = (format?: string): void => {
    if (format === undefined) delete process.env.LOG_FORMAT
    else process.env.LOG_FORMAT = format
    ;(Logger as any).instance = null
    const root = Logger.getInstance()
    ;(root as any).logger.level = 'debug'
    records = []
    sinon.stub((root as any).logger, 'log').callsFake((r: any) => {
      records.push(r)
    })
  }
  const lines = (): Record<string, any>[] => records.filter((r) => r.message === 'upstream.call.end')
  const winstonRoot = (): winston.Logger => (Logger.getInstance() as any).logger
  /** What the console transport prints for a record, under the current LOG_FORMAT. */
  const consoleLine = (r: Record<string, unknown>): string => {
    const shaped = winstonRoot().format.transform({ ...r } as any, {}) as Record<string | symbol, unknown>
    const console = winstonRoot().transports.find((t) => t instanceof winston.transports.Console)!
    return String((console.format!.transform(shaped as any, {}) as Record<symbol, unknown>)[MESSAGE])
  }
  const inRequest = <T>(rootId: string, fn: () => Promise<T>): Promise<T> =>
    runWithRequestContext({ rootId }, () => {
      setContextIdentity({ orgId: ORG, userId: USER })
      return fn()
    })

  before(async () => {
    server = http.createServer((req, res) => {
      const path = (req.url ?? '').split('?')[0]!
      received.push({ path, requestId: req.headers['x-request-id'] as string | undefined })
      if (path.endsWith('/drop')) {
        req.socket.destroy()
        return
      }
      if (path.endsWith('/fail')) {
        res.writeHead(500, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ detail: `graph query failed for ${BODY_SECRET}` }))
        return
      }
      if (path.includes('/health')) {
        res.writeHead(503, { 'content-type': 'application/json' })
        res.end('{"status":"unhealthy"}')
        return
      }
      if (path.endsWith('/missing')) {
        res.writeHead(404, { 'content-type': 'application/json' })
        res.end('{"detail":"no such thing"}')
        return
      }
      if (path.endsWith('/empty')) {
        res.writeHead(204)
        res.end()
        return
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end('{"ok":true}')
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  after(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  beforeEach(() => {
    received = []
    sinon.stub(seam, 'getProvider').returns(
      Object.assign(new seam.DefaultLoggingProvider(), { fileLoggingEnabled: () => false }),
    )
    freshLogger()
  })

  afterEach(() => {
    sinon.restore()
    registerUpstreams({})
    delete process.env.LOG_FORMAT
    ;(Logger as any).instance = null
  })

  describe('upstreamTarget', () => {
    beforeEach(() => {
      registerUpstreams({ connector: ['http://connector:8088'], query: ['http://query:8000/'] })
    })

    it('templates every id-like path segment and keeps words, versions and snake_case', () => {
      const route = (path: string): string => upstreamTarget(`http://connector:8088${path}`).route
      expect(route('/api/v1/connectors/65f0a1b2c3d4e5f6a7b8c9d2/stats')).to.equal('/api/v1/connectors/:id/stats')
      expect(route('/api/v1/kb/3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90/records/42')).to.equal('/api/v1/kb/:id/records/:id')
      expect(route('/api/v1/knowledge-base/stream_ok/deadbeefcafe')).to.equal('/api/v1/knowledge-base/stream_ok/:id')
      for (const segment of ['My%20Report', 'alice@example.com', 'Report.pdf', 'Uppercase', '123']) {
        expect(route(`/api/v1/files/${segment}`), segment).to.equal('/api/v1/files/:id')
      }
    })

    it('never carries the query, the fragment or userinfo; a third party is its host only', () => {
      const internal = upstreamTarget('http://connector:8088/api/v1/stats?connector_id=c1&token=t1#frag')
      expect(internal).to.include({ upstream: 'connector', route: '/api/v1/stats', internal: true })
      const external = upstreamTarget('https://user:pw@oauth2.googleapis.com/token?code=x')
      expect(external).to.include({ upstream: 'external', route: 'oauth2.googleapis.com', internal: false })
    })

    it('matches a registered service by exact origin and on a path-segment boundary', () => {
      registerUpstreams({ nodejs: ['http://localhost:3000'], api: ['https://api.x.com'], kb: ['http://h:1/api'] })
      expect(upstreamTarget('http://localhost:3000/api/v1/users').upstream).to.equal('nodejs')
      expect(upstreamTarget('http://localhost:30001/api/v1/users').upstream).to.equal('external')
      expect(upstreamTarget('https://api.x.com.evil.net/steal').upstream).to.equal('external')
      expect(upstreamTarget('http://h:1/api/v1').upstream).to.equal('kb')
      expect(upstreamTarget('http://h:1/apiv2/v1').upstream).to.equal('external')
    })

    it('the longest registered base wins; re-registering replaces; unregistered is external', () => {
      registerUpstreams({ nodejs: ['http://gw:3000'], kb: ['http://gw:3000/api/v1/kb'] })
      expect(upstreamTarget('http://gw:3000/api/v1/kb/x').upstream).to.equal('kb')
      expect(upstreamTarget('http://gw:3000/api/v1/users').upstream).to.equal('nodejs')
      registerUpstreams({ query: ['http://query:8000'] })
      expect(upstreamTarget('http://gw:3000/api/v1/kb/x').upstream).to.equal('external')
      expect(upstreamTarget('not a url')).to.include({ upstream: 'external', internal: false })
    })
  })

  describe('upstreamFetch (the service commands)', () => {
    beforeEach(() => {
      registerUpstreams({ connector: [origin] })
    })

    it('a 2xx is one DEBUG line with the fields, typed, and nothing at INFO', async () => {
      await inRequest('rq-1', () =>
        upstreamFetch(`${origin}/api/v1/connectors/65f0a1b2c3d4e5f6a7b8c9d2/ok?token=query-secret`, {
          method: 'GET',
          headers: { authorization: `Bearer ${TOKEN}` },
        }),
      )
      expect(lines()).to.have.length(1)
      const [line] = lines()
      expect(line).to.include({
        level: 'debug', service: 'UpstreamCall', upstream: 'connector', method: 'GET',
        route: '/api/v1/connectors/:id/ok', status: 200, attempt: 1, outcome: 'success',
        request_id: 'rq-1', org_id: ORG, user_id: USER, event_name: 'upstream.call.end',
      })
      expect(line!.duration_ms).to.be.a('number').and.satisfy(Number.isInteger)
      expect(line).to.not.have.any.keys('error_class', 'network_code')
      expect(line!.metadata).to.equal(undefined)
      expect(consoleLine(line!)).to.include(
        'upstream.call.end | org_id=64f0c0ffee0000000000000a user_id=64f0c0ffee0000000000000b upstream=connector method=GET route=/api/v1/connectors/:id/ok status=200 duration_ms=',
      )
      expect(consoleLine(line!)).to.include(' attempt=1 outcome=success')
    })

    for (const [path, level, errorClass] of [
      ['/x/missing', 'warning', 'not_found'],
      ['/x/fail', 'warning', 'upstream_5xx'],
    ] as const) {
      it(`an answered ${path} is ${level} ${errorClass}, with its status and no body`, async () => {
        await upstreamFetch(`${origin}${path}`, { method: 'POST', body: '{}' })
        expect(lines()).to.have.length(1)
        expect(lines()[0]).to.include({ level, error_class: errorClass, outcome: 'failure', method: 'POST' })
        expect(lines()[0]).to.not.have.property('network_code')
        expect(JSON.stringify(records)).to.not.include(BODY_SECRET)
      })
    }

    const failures: Array<[string, () => unknown, string, string, string | undefined]> = [
      ['refused', refusedFetch, 'warning', 'unavailable', 'ECONNREFUSED'],
      ['timed out', () => new DOMException('The operation was aborted due to timeout', 'TimeoutError'), 'warning', 'timeout', undefined],
      ['cancelled by us', () => new DOMException('This operation was aborted', 'AbortError'), 'info', 'cancelled', undefined],
      ['our bug (an invalid header)', () => new TypeError('Invalid header value'), 'error', 'internal', undefined],
    ]
    for (const [name, error, level, errorClass, code] of failures) {
      it(`a call that got no answer (${name}) is ${level} ${errorClass} and rethrows the same error`, async () => {
        const failure = error()
        sinon.stub(global, 'fetch').rejects(failure)
        let thrown: unknown
        try {
          await upstreamFetch(`${origin}/x`, { method: 'GET' }, 2)
        } catch (e) {
          thrown = e
        }
        expect(thrown).to.equal(failure)
        expect(lines()).to.have.length(1)
        expect(lines()[0]).to.include({ level, error_class: errorClass, outcome: 'failure', attempt: 2 })
        expect(lines()[0]).to.not.have.property('status')
        if (code === undefined) expect(lines()[0]).to.not.have.property('network_code')
        else expect(lines()[0]).to.include({ network_code: code })
        expect(JSON.stringify(records)).to.not.include('fetch failed').and.not.include('Invalid header')
      })
    }

    it('a socket reset before the answer is unavailable with network_code=UND_ERR_SOCKET', async () => {
      let thrown: unknown
      try {
        await upstreamFetch(`${origin}/x/drop`, { method: 'GET' })
      } catch (e) {
        thrown = e
      }
      expect(thrown).to.be.instanceOf(TypeError)
      expect(lines()[0]).to.include({ level: 'warning', error_class: 'unavailable', network_code: 'UND_ERR_SOCKET' })
    })

    it('an upstream 408 is a timeout', async () => {
      sinon.stub(global, 'fetch').resolves(new Response(null, { status: 408 }))
      await upstreamFetch(`${origin}/x`, { method: 'GET' })
      expect(lines()[0]).to.include({ level: 'warning', status: 408, error_class: 'timeout' })
    })

    it('holds a 2xx with a body for the caller, who ends it once', async () => {
      let held: Parameters<typeof endUpstreamCall>[0] | undefined
      const response = await upstreamFetch(`${origin}/x/ok`, { method: 'POST' }, 1, (call) => {
        held = call
      })
      await response.text()
      expect(lines()).to.have.length(0)
      endUpstreamCall(held!, { status: 200 })
      endUpstreamCall(held!, { status: 200, error: new Error('late') })
      expect(lines()).to.have.length(1)
      expect(lines()[0]).to.include({ level: 'debug', outcome: 'success' })
    })

    it('ends a held answer itself when it is not a 2xx with a body', async () => {
      const hold = sinon.spy()
      await upstreamFetch(`${origin}/x/empty`, { method: 'DELETE' }, 1, hold)
      await upstreamFetch(`${origin}/x/fail`, { method: 'POST' }, 1, hold)
      expect(hold.called).to.equal(false)
      expect(lines().map((l) => l.status)).to.deep.equal([204, 500])
    })

    it('no sink carries the bearer token, the query, the body or an error message', async () => {
      for (const format of [undefined, 'json']) {
        freshLogger(format)
        await upstreamFetch(`${origin}/x/fail?access_token=query-secret`, {
          method: 'POST',
          headers: { authorization: `Bearer ${TOKEN}` },
          body: JSON.stringify({ password: 'hunter2' }),
        })
        const printed = consoleLine(lines()[0]!)
        for (const secret of [TOKEN, 'query-secret', 'access_token', 'hunter2', BODY_SECRET]) {
          expect(printed, `${format} ${secret}`).to.not.include(secret)
          expect(JSON.stringify(records), secret).to.not.include(secret)
        }
      }
    })

    it('the fields are top-level JSON keys under LOG_FORMAT=json', async () => {
      freshLogger('json')
      sinon.stub(global, 'fetch').rejects(refusedFetch())
      await upstreamFetch(`${origin}/x`, { method: 'GET' }, 3).catch(() => undefined)
      const json = JSON.parse(consoleLine(lines()[0]!))
      expect(json).to.include({
        message: 'upstream.call.end', upstream: 'connector', attempt: 3, network_code: 'ECONNREFUSED', error_class: 'unavailable',
      })
      expect(json).to.not.have.property('metadata')
    })

    it('a logger that throws never turns the call into a failure', async () => {
      for (const level of ['debug', 'warn'] as const) {
        sinon.stub(Logger.prototype, level).throws(new Error('transport down'))
      }
      const response = await upstreamFetch(`${origin}/x/ok`, { method: 'GET' })
      expect(response.status).to.equal(200)
      const failed = await upstreamFetch(`${origin}/x/fail`, { method: 'GET' })
      expect(failed.status).to.equal(500)
      const client = axios.create()
      installUpstreamLogging(client)
      expect((await client.get(`${origin}/x/ok`)).status).to.equal(200)
    })

    describe('health probes are DEBUG whatever the outcome', () => {
      it('for a PipesHub service, /health and /health/... ', async () => {
        await upstreamFetch(`${origin}/health`, { method: 'GET' })
        await upstreamFetch(`${origin}/api/v1/health/graph-db`, { method: 'GET' })
        expect(lines().map((l) => [l.level, l.status, l.error_class])).to.deep.equal([
          ['debug', 503, 'unavailable'],
          ['debug', 503, 'unavailable'],
        ])
      })

      it('for an external probe that is refused (through axios: upstreamFetch never sends to one)', async () => {
        registerUpstreams({})
        const client = axios.create({
          adapter: async (config) => {
            throw new AxiosError('connect ECONNREFUSED 127.0.0.1:1', 'ECONNREFUSED', config as InternalAxiosRequestConfig, {})
          },
        })
        installUpstreamLogging(client)
        await client.get('http://localhost:8002/health').catch(() => undefined)
        expect(lines()[0]).to.include({ level: 'debug', upstream: 'external', error_class: 'unavailable' })
      })

      it('but not a route that only starts with the word', async () => {
        await upstreamFetch(`${origin}/api/v1/health-check/fail`, { method: 'GET' })
        expect(lines()[0]).to.include({ level: 'warning', error_class: 'upstream_5xx' })
      })
    })
  })

  describe('upstreamFetch sends only to a registered PipesHub service', () => {
    const QUERY = 'http://query.internal:8000'
    let fetchStub: sinon.SinonStub

    beforeEach(() => {
      registerUpstreams({ query: [QUERY], kb: ['http://gw:3000/api/v1/kb'] })
      fetchStub = sinon.stub(global, 'fetch').resolves(new Response(null, { status: 204 }))
    })

    const refusal = async (url: string): Promise<unknown> =>
      upstreamFetch(url, { method: 'DELETE' }).then(
        () => expect.fail(`${url} was sent`),
        (error: unknown) => error,
      )

    const refused: Array<[string, string, string]> = [
      ['an unregistered origin', 'http://169.254.169.254/latest/meta-data/', 'not a registered PipesHub service'],
      ['a registered host on another port', 'http://query.internal:8001/api/v1/x', 'not a registered PipesHub service'],
      ['a registered host and port over https', 'https://query.internal:8000/api/v1/x', 'not a registered PipesHub service'],
      ['a path outside the base path', 'http://gw:3000/api/v1/users', 'not a registered PipesHub service'],
      ['a path that only starts with the base path', 'http://gw:3000/api/v1/kbx', 'not a registered PipesHub service'],
      ['an encoded dot-dot segment out of the base path', 'http://gw:3000/api/v1/kb/%2e%2e/users', 'not a registered PipesHub service'],
      ['an encoded ../ out of the base path', 'http://gw:3000/api/v1/kb/%2E%2E%2Fusers', 'not a registered PipesHub service'],
      ['a ..%2F out of the base path', 'http://gw:3000/api/v1/kb/..%2Fusers', 'not a registered PipesHub service'],
      ['a ..%5C out of the base path', 'http://gw:3000/api/v1/kb/..%5Cusers', 'not a registered PipesHub service'],
      ['a malformed escape', 'http://gw:3000/api/v1/kb/%zz', 'not a registered PipesHub service'],
      ['embedded credentials on a registered origin', 'http://user:pass@query.internal:8000/api/v1/x', 'must not contain embedded credentials'],
      ['a data: URL', 'data:text/plain,hi', 'Blocked request: URL scheme must be http or https'],
      ['a file: URL', 'file:///etc/passwd', 'Blocked request: URL scheme must be http or https'],
      ['a relative URL', '/api/v1/chat/attachments/rec-1', 'URL is not valid'],
    ]
    for (const [name, url, reason] of refused) {
      it(`refuses ${name} before fetch, with no call line and no URL in the error`, async () => {
        const error = await refusal(url)

        expect(error).to.be.instanceOf(InternalServerError)
        expect((error as Error).message).to.include(reason)
        expect((error as Error).message).to.not.include('169.254').and.not.include('query.internal').and.not.include('gw:3000')
        expect((error as Error).message).to.not.include('data:').and.not.include('file:')
        expect(fetchStub.called, 'fetch reached').to.equal(false)
        expect(lines()).to.deep.equal([])
      })
    }

    it('returns exactly the URL it checked, normalised as URL does', () => {
      registerUpstreams({ query: [QUERY], kb: ['http://gw:3000/api/v1/kb'], ipv6: ['http://[::1]:9000'], tls: ['https://secure.internal'] })

      for (const url of [
        `${QUERY}/api/v1/x?q=a%20b&r=1#frag`,
        'http://QUERY.internal:8000/api/v1/./x/../y',
        'http://gw:3000/api/v1/kb/a%2Fb',
        'http://[0:0::1]:9000/z',
        'https://secure.internal:443/p?x',
        `${QUERY}/items?`,
        `${QUERY}/items#`,
        QUERY,
      ]) {
        expect(checkUpstreamUrl(url), url).to.equal(new URL(url).href)
      }
    })

        it('refuses everything while nothing is registered', async () => {
      registerUpstreams({})

      expect(await refusal(`${QUERY}/api/v1/x`)).to.be.instanceOf(InternalServerError)
      expect(fetchStub.called).to.equal(false)
    })

    it('holds to the latest registration: a replaced base is refused, the new one sent', async () => {
      registerUpstreams({ query: ['http://query-2.internal:8000'] })

      expect(await refusal(`${QUERY}/api/v1/x`)).to.be.instanceOf(InternalServerError)
      await upstreamFetch('http://query-2.internal:8000/api/v1/x', { method: 'GET' })
      expect(fetchStub.getCalls().map((c) => c.args[0])).to.deep.equal(['http://query-2.internal:8000/api/v1/x'])
    })

    it('sends a registered URL normalised (host case, default port, an encoded slash inside the base), and writes its call line', async () => {
      registerUpstreams({ query: [QUERY], nodejs: ['http://gateway'], kb: ['http://gw:3000/api/v1/kb'] })

      await upstreamFetch('http://QUERY.internal:8000/api/v1/x', { method: 'GET' })
      await upstreamFetch('http://gateway:80/api/v1/y', { method: 'GET' })
      await upstreamFetch('http://gw:3000/api/v1/kb', { method: 'GET' })
      // An encoded slash that stays inside the base path is an id, not a climb.
      await upstreamFetch('http://gw:3000/api/v1/kb/a%2Fb', { method: 'GET' })

      expect(fetchStub.getCalls().map((c) => c.args[0])).to.deep.equal([
        'http://query.internal:8000/api/v1/x',
        'http://gateway/api/v1/y',
        'http://gw:3000/api/v1/kb',
        'http://gw:3000/api/v1/kb/a%2Fb',
      ])
      expect(lines().map((l) => l.upstream)).to.deep.equal(['query', 'nodejs', 'kb', 'kb'])
    })
  })

  describe('upstreamFetch follows a redirect only to a registered service', () => {
    let target: http.Server
    let other: http.Server
    let base: string
    let otherBase: string
    let seen: Array<{ method: string; path: string; body: string }>
    let sent: Array<{ origin: string; headers: http.IncomingHttpHeaders }>

    before(async () => {
      // A second registered service, on another origin.
      other = http.createServer((req, res) => {
        sent.push({ origin: otherBase, headers: req.headers })
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end('{"ok":true}')
      })
      await new Promise<void>((resolve) => other.listen(0, '127.0.0.1', resolve))
      otherBase = `http://127.0.0.1:${(other.address() as AddressInfo).port}`
      target = http.createServer((req, res) => {
        let body = ''
        req.on('data', (chunk: Buffer) => (body += chunk.toString()))
        req.on('end', () => {
          const path = req.url ?? ''
          seen.push({ method: req.method ?? '', path, body })
          sent.push({ origin: base, headers: req.headers })
          const to = (status: number, location: string): void => {
            res.writeHead(status, { location })
            res.end()
          }
          if (path === '/r/slash') return to(307, '/r/slash/')
          if (path === '/r/cross') return to(307, `${otherBase}/landed`)
          if (path === '/r/same') return to(307, '/r/landed')
          if (path === '/r/malformed') return to(302, 'http://[')
          if (path === '/r/custom-scheme') return to(302, 'private-test-marker:')
          // .invalid never resolves, so this test sends nothing off the machine on any tree.
          if (path === '/r/away') return to(302, 'http://unregistered.invalid/latest/meta-data/')
          if (path === '/r/other-port') return to(301, `${base.replace(/:\d+$/, ':1')}/x`)
          if (path === '/r/see-other') return to(303, '/r/done')
          if (path === '/r/loop') return to(307, '/r/loop')
          if (path === '/r/no-location') {
            res.writeHead(302)
            return void res.end()
          }
          res.writeHead(200, { 'content-type': 'application/json' })
          res.end('{"ok":true}')
        })
      })
      await new Promise<void>((resolve) => target.listen(0, '127.0.0.1', resolve))
      base = `http://127.0.0.1:${(target.address() as AddressInfo).port}`
    })

    after(async () => {
      for (const server of [target, other]) {
        server.closeAllConnections()
        await new Promise<void>((resolve) => server.close(() => resolve()))
      }
    })

    beforeEach(() => {
      seen = []
      sent = []
      registerUpstreams({ connector: [base], query: [otherBase] })
    })

    const CREDENTIALS = { Authorization: 'Bearer svc-token', Cookie: 'sid=1', 'Proxy-Authorization': 'Basic cHJveHk=' }
    const shapes: Array<[string, () => HeadersInit]> = [
      ['a plain object (any case)', () => ({ ...CREDENTIALS, 'x-keep': 'yes' })],
      ['a Headers instance', () => new Headers({ ...CREDENTIALS, 'x-keep': 'yes' })],
      ['an array of tuples', () => [...Object.entries(CREDENTIALS), ['x-keep', 'yes']] as Array<[string, string]>],
    ]
    for (const [shape, headers] of shapes) {
      it(`drops the credentials on a hop to another origin, as fetch does, given ${shape}`, async () => {
        const response = await upstreamFetch(`${base}/r/cross`, { method: 'GET', headers: headers() })

        expect(response.status).to.equal(200)
        expect(sent.map((s) => s.origin)).to.deep.equal([base, otherBase])
        expect(sent[0]!.headers).to.include({ authorization: 'Bearer svc-token', cookie: 'sid=1' })
        expect(sent[1]!.headers).to.not.have.any.keys('authorization', 'cookie', 'proxy-authorization')
        expect(sent[1]!.headers).to.include({ 'x-keep': 'yes' })
      })
    }

    it('keeps the credentials on a hop within the same origin', async () => {
      await upstreamFetch(`${base}/r/same`, { method: 'GET', headers: { ...CREDENTIALS } })

      expect(sent.map((s) => s.origin)).to.deep.equal([base, base])
      expect(sent[1]!.headers).to.include({ authorization: 'Bearer svc-token', cookie: 'sid=1', 'proxy-authorization': 'Basic cHJveHk=' })
    })

    const refusedWith = async (path: string, init: RequestInit = { method: 'GET' }): Promise<unknown> =>
      upstreamFetch(`${base}${path}`, init).then(
        () => expect.fail(`${path} was answered`),
        (error: unknown) => error,
      )

    it('follows a redirect within the registered service, keeping the method and body of a 307, one call line per request', async () => {
      const response = await upstreamFetch(`${base}/r/slash`, { method: 'POST', body: '{"q":1}' })

      expect(response.status).to.equal(200)
      expect(seen).to.deep.equal([
        { method: 'POST', path: '/r/slash', body: '{"q":1}' },
        { method: 'POST', path: '/r/slash/', body: '{"q":1}' },
      ])
      expect(lines().map((l) => [l.status, l.route])).to.deep.equal([
        [307, '/r/slash'],
        [200, '/r/slash/'],
      ])
    })

    it('turns a POST into a GET without a body on a 303, as fetch does', async () => {
      await upstreamFetch(`${base}/r/see-other`, { method: 'POST', body: 'x' })

      expect(seen.map((s) => [s.method, s.path, s.body])).to.deep.equal([
        ['POST', '/r/see-other', 'x'],
        ['GET', '/r/done', ''],
      ])
    })

    it('refuses a redirect to an unregistered origin, or another port, without sending it', async () => {
      const fetchSpy = sinon.spy(global, 'fetch')

      for (const path of ['/r/away', '/r/other-port']) {
        const error = await refusedWith(path)
        expect(error, path).to.be.instanceOf(InternalServerError)
        expect((error as Error).message).to.include('not a registered PipesHub service').and.not.include('unregistered.invalid')
      }
      expect(fetchSpy.getCalls().map((c) => new URL(c.args[0] as string).pathname)).to.deep.equal(['/r/away', '/r/other-port'])
    })

    it('refuses a malformed Location like an invalid URL, with no cause and without its text, marked as not to be retried', async () => {
      const error = await refusedWith('/r/malformed')

      expect(error).to.be.instanceOf(InternalServerError)
      expect((error as Error).message).to.equal('Blocked request: URL is not valid')
      expect((error as { cause?: unknown }).cause).to.equal(undefined)
      expect(isUpstreamRefusal(error)).to.equal(true)
      expect(seen).to.have.length(1)
    })

    it('refuses a redirect to another scheme with fixed text that never carries the scheme', async () => {
      const error = await refusedWith('/r/custom-scheme')

      expect(error).to.be.instanceOf(InternalServerError)
      expect((error as Error).message).to.equal('Blocked request: URL scheme must be http or https')
      expect((error as Error).message).to.not.include('private-test-marker')
      expect(isUpstreamRefusal(error)).to.equal(true)
      expect(seen).to.have.length(1)
    })

    it('refuses a cross-origin redirect in same-origin mode, before sending it, without retry', async () => {
      const error = await refusedWith('/r/cross', { method: 'POST', body: 'x', mode: 'same-origin' })

      expect(error).to.be.instanceOf(InternalServerError)
      expect((error as Error).message).to.equal('Blocked request: cross-origin redirect in same-origin mode')
      expect(isUpstreamRefusal(error)).to.equal(true)
      expect(sent.map((s) => s.origin)).to.deep.equal([base])
    })

    it('follows a same-origin redirect in same-origin mode, and a cross-origin one in the default mode', async () => {
      await upstreamFetch(`${base}/r/same`, { method: 'GET', mode: 'same-origin' })
      await upstreamFetch(`${base}/r/cross`, { method: 'GET' })

      expect(sent.map((s) => s.origin)).to.deep.equal([base, base, base, otherBase])
    })

    it('marks a refused redirect and the hop limit as refusals, and a transport failure not', async () => {
      expect(isUpstreamRefusal(await refusedWith('/r/away'))).to.equal(true)
      expect(isUpstreamRefusal(await refusedWith('/r/loop'))).to.equal(true)
      expect(isUpstreamRefusal(new TypeError('fetch failed'))).to.equal(false)
      expect(isUpstreamRefusal(new InternalServerError('Blocked request: not a registered PipesHub service'))).to.equal(false)
    })

    it('stops a redirect loop after 20 hops, and releases every redirect body, the last one too', async () => {
      const cancel = sinon.spy(ReadableStream.prototype, 'cancel')

      const error = await refusedWith('/r/loop')

      expect(error).to.be.instanceOf(InternalServerError)
      expect((error as Error).message).to.include('too many redirects')
      expect(seen).to.have.length(21)
      expect(cancel.callCount).to.equal(21)
    })

    const BODY_HEADERS = {
      'Content-Type': 'text/plain',
      'Content-Length': '1',
      'Content-Language': 'en',
      'Content-Location': '/doc',
      'Content-Encoding': 'identity',
    }
    const bodyShapes: Array<[string, () => HeadersInit]> = [
      ['a plain object (any case)', () => ({ ...BODY_HEADERS, 'x-keep': 'yes' })],
      ['a Headers instance', () => new Headers({ ...BODY_HEADERS, 'x-keep': 'yes' })],
      ['an array of tuples', () => [...Object.entries(BODY_HEADERS), ['x-keep', 'yes']] as Array<[string, string]>],
    ]
    for (const [shape, headers] of bodyShapes) {
      it(`sends the GET a 303 turns a POST into without the body headers, as fetch does, given ${shape}`, async () => {
        await upstreamFetch(`${base}/r/see-other`, { method: 'POST', body: 'x', headers: headers() })

        expect(seen.map((s) => s.method)).to.deep.equal(['POST', 'GET'])
        expect(sent[0]!.headers).to.include({ 'content-type': 'text/plain', 'content-length': '1' })
        expect(sent[1]!.headers).to.not.have.any.keys(
          'content-type', 'content-length', 'content-language', 'content-location', 'content-encoding',
        )
        expect(sent[1]!.headers).to.include({ 'x-keep': 'yes' })
      })
    }

    it('keeps the method, body and body headers on a 307', async () => {
      await upstreamFetch(`${base}/r/slash`, { method: 'POST', body: 'x', headers: { ...BODY_HEADERS } })

      expect(seen.map((s) => [s.method, s.body])).to.deep.equal([['POST', 'x'], ['POST', 'x']])
      expect(sent[1]!.headers).to.include({ 'content-type': 'text/plain', 'content-length': '1', 'content-language': 'en' })
    })

    it('returns a 3xx without a Location as it is', async () => {
      const response = await upstreamFetch(`${base}/r/no-location`, { method: 'GET' })

      expect(response.status).to.equal(302)
      expect(seen).to.have.length(1)
    })
  })

  describe('installUpstreamLogging (axios)', () => {
    const refusedAdapter: AxiosAdapter = async (config) => {
      throw new AxiosError('connect ECONNREFUSED 127.0.0.1:1', 'ECONNREFUSED', config as InternalAxiosRequestConfig, {})
    }

    beforeEach(() => {
      registerUpstreams({ query: [origin] })
    })

    const retryingClient = () => {
      const client = axios.create({ adapter: refusedAdapter })
      axiosRetry(client, { ...RETRY_POLICY, retryDelay: () => 0 })
      installUpstreamLogging(client)
      return client
    }

    it("counts every axios-retry attempt with the app's retry rule", async () => {
      await retryingClient().get(`${origin}/api/v1/skills/`).catch(() => undefined)
      expect(lines().map((l) => [l.attempt, l.level, l.error_class, l.network_code])).to.deep.equal([
        [1, 'warning', 'unavailable', 'ECONNREFUSED'],
        [2, 'warning', 'unavailable', 'ECONNREFUSED'],
        [3, 'warning', 'unavailable', 'ECONNREFUSED'],
        [4, 'warning', 'unavailable', 'ECONNREFUSED'],
      ])
      expect(lines()[0]).to.include({ upstream: 'query', method: 'GET', route: '/api/v1/skills/' })
    })

    it("writes one line for a refused POST: the app's retry rule never sends it again", async () => {
      await retryingClient().post(`${origin}/api/v1/skills/`, {}).catch(() => undefined)
      expect(lines().map((l) => [l.attempt, l.method, l.error_class])).to.deep.equal([[1, 'POST', 'unavailable']])
    })

    it('a status the caller accepts (validateStatus) or refuses is an answer, never a transport failure', async () => {
      const client = axios.create()
      installUpstreamLogging(client)
      await client.post(`${origin}/x/fail`, {}, { validateStatus: () => true })
      await client.get(`${origin}/x/missing`).catch(() => undefined)
      expect(lines().map((l) => [l.method, l.status, l.level, l.error_class])).to.deep.equal([
        ['POST', 500, 'warning', 'upstream_5xx'],
        ['GET', 404, 'warning', 'not_found'],
      ])
      expect(lines().every((l) => l.network_code === undefined)).to.equal(true)
      expect(JSON.stringify(records)).to.not.include(BODY_SECRET)
    })

    it("sends the request's root id to a PipesHub service, over a copied client id in any case", async () => {
      const client = axios.create()
      installUpstreamLogging(client)
      await inRequest('rqbad', async () => {
        await client.get(`${origin}/a/ok`, { headers: { 'x-request-id': 'rq bad!' } })
        await client.get(`${origin}/b/ok`, { headers: { 'X-Request-ID': 'rq bad!', 'X-Other': 'kept' } })
        await client.get(`${origin}/c/ok`)
      })
      expect(received.map((r) => r.requestId)).to.deep.equal(['rqbad', 'rqbad', 'rqbad'])
    })

    it('adds no request id to a third party', async () => {
      registerUpstreams({})
      const client = axios.create()
      installUpstreamLogging(client)
      await inRequest('rq-ext', () => client.get(`${origin}/ext/ok`))
      expect(received[0]!.requestId).to.equal(undefined)
      expect(lines()[0]).to.include({ upstream: 'external', route: origin.replace('http://', '') })
    })

    it('is idempotent, also for an instance created from an instrumented one', async () => {
      const parent = axios.create()
      installUpstreamLogging(parent)
      installUpstreamLogging(parent)
      const child = parent.create()
      installUpstreamLogging(child)
      await parent.get(`${origin}/p/ok`)
      await child.get(`${origin}/c/ok`)
      expect(lines()).to.have.length(2)
    })
  })
})
