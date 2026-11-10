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
import {
  endUpstreamCall,
  installUpstreamLogging,
  registerUpstreams,
  upstreamFetch,
  upstreamTarget,
} from '../../../src/libs/http/upstream-call'

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
    sinon.stub((root as any).logger, 'log').callsFake((r: Record<string, any>) => {
      records.push(r)
    })
  }
  const lines = (): Record<string, any>[] => records.filter((r) => r.message === 'upstream.call.end')
  const winstonRoot = (): winston.Logger => (Logger.getInstance() as any).logger
  /** What the console transport prints for a record, under the current LOG_FORMAT. */
  const consoleLine = (r: Record<string, unknown>): string => {
    const shaped = winstonRoot().format.transform({ ...r }, {}) as Record<string | symbol, unknown>
    const console = winstonRoot().transports.find((t) => t instanceof winston.transports.Console)!
    return String((console.format!.transform(shaped, {}) as Record<symbol, unknown>)[MESSAGE])
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

      it('for an external probe that is refused', async () => {
        registerUpstreams({})
        sinon.stub(global, 'fetch').rejects(refusedFetch())
        await upstreamFetch('http://localhost:8002/health', { method: 'GET' }).catch(() => undefined)
        expect(lines()[0]).to.include({ level: 'debug', upstream: 'external', error_class: 'unavailable' })
      })

      it('but not a route that only starts with the word', async () => {
        await upstreamFetch(`${origin}/api/v1/health-check/fail`, { method: 'GET' })
        expect(lines()[0]).to.include({ level: 'warning', error_class: 'upstream_5xx' })
      })
    })
  })

  describe('installUpstreamLogging (axios)', () => {
    const refusedAdapter: AxiosAdapter = async (config) => {
      throw new AxiosError('connect ECONNREFUSED 127.0.0.1:1', 'ECONNREFUSED', config as InternalAxiosRequestConfig, {})
    }

    beforeEach(() => {
      registerUpstreams({ query: [origin] })
    })

    it("counts every axios-retry attempt with the app's retry rule", async () => {
      const client = axios.create({ adapter: refusedAdapter })
      axiosRetry(client, {
        retries: 3,
        retryDelay: () => 0,
        retryCondition: (error) =>
          !!(axiosRetry.isNetworkOrIdempotentRequestError(error) || (error.response && error.response.status >= 500)),
      })
      installUpstreamLogging(client)
      await client.get(`${origin}/api/v1/skills/`).catch(() => undefined)
      expect(lines().map((l) => [l.attempt, l.level, l.error_class, l.network_code])).to.deep.equal([
        [1, 'warning', 'unavailable', 'ECONNREFUSED'],
        [2, 'warning', 'unavailable', 'ECONNREFUSED'],
        [3, 'warning', 'unavailable', 'ECONNREFUSED'],
        [4, 'warning', 'unavailable', 'ECONNREFUSED'],
      ])
      expect(lines()[0]).to.include({ upstream: 'query', method: 'GET', route: '/api/v1/skills/' })
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
