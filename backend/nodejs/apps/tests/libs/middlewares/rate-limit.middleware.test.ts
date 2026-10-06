import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import express from 'express'
import { AddressInfo } from 'net'
import {
  CALLER_ROLE_LOOKUP_PATH,
  SERVICE_AUTHORIZATION_HEADER,
  createAuthRateLimiter,
  createGlobalRateLimiter,
  createKeyedRateLimiter,
  createOAuthClientRateLimiter,
  createSkillsImportRateLimiter,
} from '../../../src/libs/middlewares/rate-limit.middleware'
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum'
import { useRateLimitCache, SharedRateLimitStore, RATE_LIMIT_STORE_RETRY_MS } from '../../../src/libs/middlewares/shared-rate-limit-store'
import { ICacheService } from '../../../src/libs/services/cache/cacheService.interface'
import { realIoredis } from '../../helpers/mock-ioredis-global'
import { COLLAB_FEED_PER_MINUTE } from '../../../src/modules/enterprise_search/routes/collaboration.routes'
import { Logger } from '../../../src/libs/services/logger.service'
import { TrustProxySetting } from '../../../src/libs/utils/trust-proxy'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: {},
    body: {},
    params: {},
    query: {},
    path: '/test',
    method: 'GET',
    ip: '127.0.0.1',
    socket: { remoteAddress: '127.0.0.1' },
    get: sinon.stub(),
    app: { enabled: sinon.stub().returns(false) },
    ...overrides,
  }
}

function createMockResponse(): any {
  const headerMap: Record<string, any> = {}
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub().callsFake((key: string, value: any) => { headerMap[key] = value; return res }),
    getHeader: sinon.stub().callsFake((key: string) => headerMap[key]),
    header: sinon.stub().callsFake((key: string, value: any) => { headerMap[key] = value; return res }),
    set: sinon.stub().callsFake((key: string, value: any) => { headerMap[key] = value; return res }),
    headersSent: false,
    statusCode: 200,
  }
  res.status.callsFake((code: number) => { res.statusCode = code; return res })
  res.json.returns(res)
  res.send.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Rate Limit Middleware', () => {
  let loggerStub: sinon.SinonStubbedInstance<Logger>

  beforeEach(() => {
    loggerStub = sinon.createStubInstance(Logger)
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // createGlobalRateLimiter
  // -----------------------------------------------------------------------
  describe('createGlobalRateLimiter', () => {
    it('should return a function (RequestHandler)', () => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 100)
      expect(limiter).to.be.a('function')
    })

    it('should allow requests within the rate limit', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 100)
      const req = createMockRequest({
        ip: '10.0.0.1',
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        // next was called -> request was allowed
        expect(next.calledOnce).to.be.true
        done()
      })

      limiter(req, res, next)
    })

    it('should use userId as rate limit key when user is authenticated', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 100)
      const req = createMockRequest({
        ip: '10.0.0.2',
        user: { userId: 'user-rate-test-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.calledOnce).to.be.true
        done()
      })

      limiter(req, res, next)
    })

    it('should skip rate limiting for service (scoped token) requests', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const req = createMockRequest({
        ip: '10.0.0.3',
        tokenPayload: { orgId: 'org1', userId: 'service-user' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.called).to.be.true
        done()
      })

      limiter(req, res, next)
    })
  })

  // -----------------------------------------------------------------------
  // createOAuthClientRateLimiter
  // -----------------------------------------------------------------------
  describe('createOAuthClientRateLimiter', () => {
    it('should return a function (RequestHandler)', () => {
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 10, 'token')
      expect(limiter).to.be.a('function')
    })

    it('should allow requests within the rate limit', (done) => {
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 10, 'token')
      const req = createMockRequest({
        ip: '10.0.1.1',
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.calledOnce).to.be.true
        done()
      })

      limiter(req, res, next)
    })

    it('should use userId as rate limit key when user is authenticated', (done) => {
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 10, 'token')
      const req = createMockRequest({
        ip: '10.0.1.2',
        user: { userId: 'oauth-rate-test-user' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.calledOnce).to.be.true
        done()
      })

      limiter(req, res, next)
    })
  })

  // -----------------------------------------------------------------------
  // Internal path bypass
  // -----------------------------------------------------------------------
  describe('internal path bypass', () => {
    it('should skip rate limiting for paths containing /internal/', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const req = createMockRequest({
        ip: '10.0.4.1',
        path: '/api/v1/document/internal/upload',
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.calledOnce).to.be.true
        done()
      })

      limiter(req, res, next)
    })

    it('should skip rate limiting for paths ending with /internal', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const req = createMockRequest({
        ip: '10.0.4.2',
        path: '/api/v1/users/internal',
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.calledOnce).to.be.true
        done()
      })

      limiter(req, res, next)
    })

    it('should NOT skip rate limiting for non-internal paths', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 100)
      const req = createMockRequest({
        ip: '10.0.4.3',
        path: '/api/v1/knowledgeBase/upload',
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.calledOnce).to.be.true
        done()
      })

      limiter(req, res, next)
    })
  })

  // -----------------------------------------------------------------------
  // Rate limit exceeded (handler callback)
  // -----------------------------------------------------------------------
  describe('rate limit exceeded', () => {
    it('should return 429 when global limit is exceeded', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.5.1'

      const req1 = createMockRequest({ ip, path: '/test1' })
      const res1 = createMockResponse()
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip, path: '/test2' })
        const res2 = createMockResponse()
        const next2 = createMockNext()

        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          const body = res2.json.firstCall.args[0]
          expect(body.error).to.have.property('message')
          expect(loggerStub.warn.called).to.be.true
          done()
          return res2
        })

        limiter(req2, res2, next2)
      })

      limiter(req1, res1, next1)
    })

    it('should return 429 with user key when authenticated user exceeds limit', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const user = { userId: 'rate-exceed-user-1' }

      const req1 = createMockRequest({ ip: '10.0.5.2', user, path: '/test1' })
      const res1 = createMockResponse()
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip: '10.0.5.2', user, path: '/test2' })
        const res2 = createMockResponse()
        const next2 = createMockNext()

        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          done()
          return res2
        })

        limiter(req2, res2, next2)
      })

      limiter(req1, res1, next1)
    })

    it('should return 429 when OAuth client limit is exceeded', (done) => {
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 1, 'token')
      const ip = '10.0.5.3'

      const req1 = createMockRequest({ ip, path: '/oauth/clients' })
      const res1 = createMockResponse()
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip, path: '/oauth/clients' })
        const res2 = createMockResponse()
        const next2 = createMockNext()

        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          const body = res2.json.firstCall.args[0]
          expect(body.error.message).to.include('OAuth client')
          expect(loggerStub.warn.called).to.be.true
          done()
          return res2
        })

        limiter(req2, res2, next2)
      })

      limiter(req1, res1, next1)
    })

    it('should return 429 when skills import limit is exceeded', (done) => {
      const limiter = createSkillsImportRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.5.5'

      const req1 = createMockRequest({ ip, path: '/skills/import/npm/preview' })
      const res1 = createMockResponse()
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip, path: '/skills/import/npm/preview' })
        const res2 = createMockResponse()
        const next2 = createMockNext()

        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          const body = res2.json.firstCall.args[0]
          expect(body.error.message).to.include('skill import')
          expect(loggerStub.warn.called).to.be.true
          done()
          return res2
        })

        limiter(req2, res2, next2)
      })

      limiter(req1, res1, next1)
    })

    it('should return 429 with user key when authenticated user exceeds skills import limit', (done) => {
      const limiter = createSkillsImportRateLimiter(loggerStub as unknown as Logger, 1)
      const user = { userId: 'skills-import-rate-exceed-user' }

      const req1 = createMockRequest({ ip: '10.0.5.6', user, path: '/skills/import/url/preview' })
      const res1 = createMockResponse()
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip: '10.0.5.6', user, path: '/skills/import/url/preview' })
        const res2 = createMockResponse()
        const next2 = createMockNext()

        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          done()
          return res2
        })

        limiter(req2, res2, next2)
      })

      limiter(req1, res1, next1)
    })

    it('should return 429 when authenticated user exceeds the default 10/min skills import limit', (done) => {
      const limiter = createSkillsImportRateLimiter(loggerStub as unknown as Logger)
      const user = { userId: 'skills-import-default-limit-user' }
      const path = '/skills/import/finalize'

      const fire = (remaining: number) => {
        const req = createMockRequest({ ip: '10.0.5.7', user, path })
        const res = createMockResponse()
        const next = createMockNext()

        if (remaining === 0) {
          res.json.callsFake(() => {
            expect(res.statusCode).to.equal(429)
            done()
            return res
          })
          limiter(req, res, next)
          return
        }

        next.callsFake(() => fire(remaining - 1))
        limiter(req, res, next)
      }

      fire(10)
    })

    it('should return 429 with user key when authenticated user exceeds OAuth limit', (done) => {
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 1, 'token')
      const user = { userId: 'oauth-rate-exceed-user' }

      const req1 = createMockRequest({ ip: '10.0.5.4', user, path: '/oauth/clients' })
      const res1 = createMockResponse()
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip: '10.0.5.4', user, path: '/oauth/clients' })
        const res2 = createMockResponse()
        const next2 = createMockNext()

        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          done()
          return res2
        })

        limiter(req2, res2, next2)
      })

      limiter(req1, res1, next1)
    })
  })

  // -----------------------------------------------------------------------
  // Client IP extraction (GHSA-78gw-g2h7-xvjj)
  // -----------------------------------------------------------------------
  describe('createKeyedRateLimiter with a code', () => {
    const exceed = (options: { code?: string }, done: (body: any) => void) => {
      const limiter = createKeyedRateLimiter(loggerStub as unknown as Logger, { prefix: `p${Math.random()}`, maxRequestsPerMinute: 1, message: 'Slow down', ...options })
      const first = createMockRequest({ user: { userId: 'u-1' }, path: '/x' })
      const next1 = createMockNext()
      next1.callsFake(() => {
        const res = createMockResponse()
        res.json.callsFake((body: any) => {
          expect(res.statusCode).to.equal(429)
          done(body)
          return res
        })
        limiter(createMockRequest({ user: { userId: 'u-1' }, path: '/x' }), res, createMockNext())
      })
      limiter(first, createMockResponse(), next1)
    }

    it('SEC-17: a 429 carries the code and retryAfter under details', (done) => {
      exceed({ code: 'RATE_LIMITED' }, (body) => {
        expect(body.error.code).to.equal('RATE_LIMITED')
        expect(body.error.details.retryAfter).to.be.a('number')
        expect(body.error).to.not.have.property('retryAfter')
        done()
      })
    })

    it('without a code the historical body is unchanged', (done) => {
      exceed({}, (body) => {
        expect(body.error.code).to.equal('HTTP_TOO_MANY_REQUESTS')
        expect(body.error).to.have.property('retryAfter')
        expect(body.error).to.not.have.property('details')
        done()
      })
    })

    it('counts per user, not per address', (done) => {
      const limiter = createKeyedRateLimiter(loggerStub as unknown as Logger, { prefix: `u${Math.random()}`, maxRequestsPerMinute: 1, message: 'Slow down', code: 'RATE_LIMITED' })
      const next = createMockNext()
      next.onFirstCall().callsFake(() => {
        const other = createMockNext()
        other.callsFake(() => done())
        limiter(createMockRequest({ user: { userId: 'u-2' }, ip: '10.9.9.1' }), createMockResponse(), other)
      })
      limiter(createMockRequest({ user: { userId: 'u-1' }, ip: '10.9.9.2' }), createMockResponse(), next)
    })
  })

  describe('Client IP extraction', () => {
    // Real Express + socket so X-Forwarded-For handling and `trust proxy` are
    // exercised end to end rather than through a hand-built req.ip.
    async function sendRequests(
      trustProxy: TrustProxySetting,
      headers: Array<Record<string, string>>,
    ): Promise<number[]> {
      const app = express()
      app.set('trust proxy', trustProxy)
      app.use(createGlobalRateLimiter(loggerStub as unknown as Logger, 1))
      app.get('/test', (_req, res) => { res.status(200).end() })
      const server = app.listen(0)
      await new Promise((resolve) => server.once('listening', resolve))
      const { port } = server.address() as AddressInfo
      try {
        const statuses: number[] = []
        for (const h of headers) {
          const response = await fetch(`http://127.0.0.1:${port}/test`, { headers: h })
          statuses.push(response.status)
        }
        return statuses
      } finally {
        server.close()
      }
    }

    it('should ignore a rotated X-Forwarded-For when no proxy is trusted', async () => {
      const statuses = await sendRequests(false, [
        { 'X-Forwarded-For': '198.51.100.1' },
        { 'X-Forwarded-For': '198.51.100.2' },
      ])
      expect(statuses).to.deep.equal([200, 429])
    })

    it('should ignore a rotated X-Real-IP', async () => {
      const statuses = await sendRequests(false, [
        { 'X-Real-IP': '198.51.100.1' },
        { 'X-Real-IP': '198.51.100.2' },
      ])
      expect(statuses).to.deep.equal([200, 429])
    })

    it('should key on the entry the trusted proxy appended, not the client-written one', async () => {
      // Client forges the leftmost entry; the trusted proxy appends the real
      // address on the right. Only the right entry must count.
      const statuses = await sendRequests(1, [
        { 'X-Forwarded-For': '6.6.6.1, 203.0.113.7' },
        { 'X-Forwarded-For': '6.6.6.2, 203.0.113.7' },
        { 'X-Forwarded-For': '6.6.6.3, 203.0.113.8' },
      ])
      expect(statuses).to.deep.equal([200, 429, 200])
    })

    it('should group IPv6 clients by /56 subnet', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const req1 = createMockRequest({ ip: '2001:db8:abcd:1200::1' })
      const res1 = createMockResponse()
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip: '2001:db8:abcd:12ff::2' })
        const res2 = createMockResponse()
        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          done()
          return res2
        })
        limiter(req2, res2, createMockNext())
      })

      limiter(req1, res1, next1)
    })

    it('should fall back to the socket address when req.ip is undefined', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const socket = { remoteAddress: '10.0.9.9' }
      const req1 = createMockRequest({ ip: undefined, socket })
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip: undefined, socket })
        const res2 = createMockResponse()
        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          done()
          return res2
        })
        limiter(req2, res2, createMockNext())
      })

      limiter(req1, createMockResponse(), next1)
    })
  })

  // -----------------------------------------------------------------------
  // createAuthRateLimiter
  // -----------------------------------------------------------------------
  describe('createAuthRateLimiter', () => {
    it('should return 429 with an auth message once the limit is exceeded', (done) => {
      const limiter = createAuthRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.7.1'
      const next1 = createMockNext()

      next1.callsFake(() => {
        const req2 = createMockRequest({ ip, path: '/authenticate' })
        const res2 = createMockResponse()
        res2.json.callsFake(() => {
          expect(res2.statusCode).to.equal(429)
          expect(res2.json.firstCall.args[0].error.message).to.include('authentication')
          done()
          return res2
        })
        limiter(req2, res2, createMockNext())
      })

      limiter(createMockRequest({ ip, path: '/initAuth' }), createMockResponse(), next1)
    })
  })

  // -----------------------------------------------------------------------
  // Shared store (PH12-07, SEC-17)
  // -----------------------------------------------------------------------
  describe('createKeyedRateLimiter with a shared store', () => {
    /** Counts like RedisService.increment: INCR, then EXPIRE. */
    class FakeCache implements ICacheService {
      readonly counts = new Map<string, number>()
      readonly ttls = new Map<string, number>()
      fail = false
      async get<T>(): Promise<T | null> { return null }
      async set(): Promise<void> {}
      async delete(key: string): Promise<void> { this.counts.delete(key) }
      async increment(key: string, options?: { ttl?: number }): Promise<number> {
        if (this.fail) throw new Error('redis down')
        const next = (this.counts.get(key) ?? 0) + 1
        this.counts.set(key, next)
        if (options?.ttl !== undefined) this.ttls.set(key, options.ttl)
        return next
      }
      async disconnect(): Promise<void> {}
      isConnected(): boolean { return !this.fail }
    }

    const hit = (limiter: any, userId: string): Promise<number> =>
      new Promise((resolve) => {
        const res = createMockResponse()
        res.json.callsFake(() => { resolve(res.statusCode); return res })
        limiter(createMockRequest({ user: { userId }, path: '/x', method: 'PUT' }), res, () => resolve(200))
      })

    const build = (prefix: string, max: number) =>
      createKeyedRateLimiter(loggerStub as unknown as Logger, { prefix, maxRequestsPerMinute: max, message: 'Slow down', code: 'RATE_LIMITED', shared: true })

    afterEach(() => useRateLimitCache(undefined))

    it('PH12-07: 21 PUTs split across two instances give exactly one 429', async () => {
      useRateLimitCache(new FakeCache())
      const a = build('collab:mutate', 20)
      const b = build('collab:mutate', 20)
      const statuses: number[] = []
      for (let i = 0; i < 21; i += 1) statuses.push(await hit(i % 2 === 0 ? a : b, 'u-1'))
      expect(statuses.filter((s) => s === 429)).to.have.length(1)
      expect(statuses[20]).to.equal(429)
    })

    it('the feed limit admits two focused devices plus ten background windows polling for a minute, then limits', async () => {
      const perMinute = 2 * 18.75 + 10 * 5
      expect(COLLAB_FEED_PER_MINUTE).to.be.at.least(Math.ceil(perMinute))
      useRateLimitCache(new FakeCache())
      const feed = build('collab:feed', COLLAB_FEED_PER_MINUTE)
      for (let i = 0; i < COLLAB_FEED_PER_MINUTE; i += 1) expect(await hit(feed, 'u-1')).to.equal(200)
      expect(await hit(feed, 'u-1')).to.equal(429)
      expect(await hit(feed, 'u-2')).to.equal(200)
    })

    it('counts per user and per prefix, and sets a TTL on the window key', async () => {
      const cache = new FakeCache()
      useRateLimitCache(cache)
      const feed = build('collab:feed', 1)
      const mutate = build('collab:mutate', 1)
      expect(await hit(feed, 'u-1')).to.equal(200)
      expect(await hit(feed, 'u-2')).to.equal(200)
      expect(await hit(mutate, 'u-1')).to.equal(200)
      expect(await hit(feed, 'u-1')).to.equal(429)
      expect([...cache.ttls.values()].every((t) => t >= 60)).to.equal(true)
    })

    const hitIp = (limiter: any, ip: string): Promise<number> =>
      new Promise((resolve) => {
        const res = createMockResponse()
        res.json.callsFake(() => { resolve(res.statusCode); return res })
        limiter(createMockRequest({ ip, path: '/initAuth', method: 'POST' }), res, () => resolve(200))
      })

    it('#19 auth: the per-IP limit counts across two instances', async () => {
      useRateLimitCache(new FakeCache())
      const a = createAuthRateLimiter(loggerStub as unknown as Logger, 3)
      const b = createAuthRateLimiter(loggerStub as unknown as Logger, 3)
      const statuses: number[] = []
      for (let i = 0; i < 4; i += 1) statuses.push(await hitIp(i % 2 === 0 ? a : b, '10.9.0.1'))
      expect(statuses).to.deep.equal([200, 200, 200, 429])
      expect(await hitIp(a, '10.9.0.2')).to.equal(200)
    })

    it('#19 oauth-client: the limit counts across two instances of one surface', async () => {
      useRateLimitCache(new FakeCache())
      const a = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 1, 'pat')
      const b = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 1, 'pat')
      expect(await hit(a, 'u-1')).to.equal(200)
      expect(await hit(b, 'u-1')).to.equal(429)
    })

    it('#19 oauth-client: different surfaces do not share a bucket', async () => {
      const cache = new FakeCache()
      useRateLimitCache(cache)
      const surfaces = ['token', 'pat', 'service-token', 'clients', 'service-accounts'] as const
      const limiters = surfaces.map((s) => createOAuthClientRateLimiter(loggerStub as unknown as Logger, 1, s))
      for (const l of limiters) expect(await hit(l, 'u-1')).to.equal(200)
      for (const l of limiters) expect(await hit(l, 'u-1')).to.equal(429)
      const keys = [...cache.counts.keys()]
      for (const s of surfaces) expect(keys.some((k) => k.startsWith(`ratelimit:oauth-client:${s}:user:u-1:`)), s).to.equal(true)
      expect(keys).to.have.length(surfaces.length)
    })

    it('#19 skills-import: the limit counts across two instances', async () => {
      useRateLimitCache(new FakeCache())
      const a = createSkillsImportRateLimiter(loggerStub as unknown as Logger, 1)
      const b = createSkillsImportRateLimiter(loggerStub as unknown as Logger, 1)
      expect(await hit(a, 'u-1')).to.equal(200)
      expect(await hit(b, 'u-1')).to.equal(429)
    })

    it('without a cache each instance counts on its own', async () => {
      const a = build('solo', 1)
      const b = build('solo', 1)
      expect(await hit(a, 'u-1')).to.equal(200)
      expect(await hit(b, 'u-1')).to.equal(200)
      expect(await hit(a, 'u-1')).to.equal(429)
    })

    it('Redis down: falls back in-process, still limits, warns once', async () => {
      const cache = new FakeCache()
      cache.fail = true
      useRateLimitCache(cache)
      const limiter = build('down', 2)
      expect(await hit(limiter, 'u-1')).to.equal(200)
      expect(await hit(limiter, 'u-1')).to.equal(200)
      expect(await hit(limiter, 'u-1')).to.equal(429)
      expect(loggerStub.warn.calledWith('Shared rate-limit store failed; counting in-process')).to.equal(true)
      expect(loggerStub.warn.args.filter((a) => a[0] === 'Shared rate-limit store failed; counting in-process')).to.have.length(1)
    })

    it('a hung cache times out and the request still proceeds', async () => {
      const cache = new FakeCache()
      cache.increment = () => new Promise<number>(() => {})
      useRateLimitCache(cache)
      const limiter = build('hung', 5)
      expect(await hit(limiter, 'u-1')).to.equal(200)
    }).timeout(3000)

    it('after a failure the cache is skipped for a while, then tried again', async () => {
      const cache = new FakeCache()
      let calls = 0
      const real = cache.increment.bind(cache)
      cache.increment = (key: string, options?: { ttl?: number }) => { calls += 1; return real(key, options) }
      cache.fail = true
      useRateLimitCache(cache)
      let now = 1_000_000_000_000
      const store = new SharedRateLimitStore('cb', loggerStub as unknown as Logger, () => now)
      store.init({ windowMs: 60_000 } as any)
      await store.increment('k')
      await store.increment('k')
      expect(calls).to.equal(1)
      cache.fail = false
      now += RATE_LIMIT_STORE_RETRY_MS
      expect((await store.increment('k')).totalHits).to.equal(1)
      expect(calls).to.equal(2)
    })

    it('a new window starts a new count', async () => {
      useRateLimitCache(new FakeCache())
      let now = 1_000_000_000_000
      const store = new SharedRateLimitStore('w', loggerStub as unknown as Logger, () => now)
      store.init({ windowMs: 60_000 } as any)
      expect((await store.increment('k')).totalHits).to.equal(1)
      expect((await store.increment('k')).totalHits).to.equal(2)
      now += 60_000
      expect((await store.increment('k')).totalHits).to.equal(1)
    })
  })

  // Needs a disposable Redis: RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6390 (the ioredis fake is bypassed).
  const redisUrl = process.env.RATE_LIMIT_REDIS_URL
  ;(redisUrl ? describe : describe.skip)('createKeyedRateLimiter against real Redis', () => {
    let client: any
    const hit = (limiter: any, userId: string): Promise<number> =>
      new Promise((resolve) => {
        const res = createMockResponse()
        res.json.callsFake(() => { resolve(res.statusCode); return res })
        limiter(createMockRequest({ user: { userId }, path: '/x', method: 'PUT' }), res, () => resolve(200))
      })

    before(() => {
      const Real = (realIoredis as any).Redis ?? (realIoredis as any).default ?? realIoredis
      client = new Real(redisUrl)
    })
    after(async () => {
      await client.quit()
      useRateLimitCache(undefined)
    })

    it('PH12-07: 21 PUTs split across two instances sharing Redis give exactly one 429', async () => {
      const cache: ICacheService = {
        get: async () => null,
        set: async () => {},
        delete: async (key) => { await client.del(key) },
        increment: async (key, options) => {
          const n = await client.incr(key)
          if (options?.ttl !== undefined) await client.expire(key, options.ttl)
          return n
        },
        disconnect: async () => {},
        isConnected: () => true,
      }
      useRateLimitCache(cache)
      const prefix = `it${Date.now()}`
      const mk = () => createKeyedRateLimiter(loggerStub as unknown as Logger, { prefix, maxRequestsPerMinute: 20, message: 'Slow down', code: 'RATE_LIMITED', shared: true })
      const a = mk()
      const b = mk()
      const statuses: number[] = []
      for (let i = 0; i < 21; i += 1) statuses.push(await hit(i % 2 === 0 ? a : b, 'u-real'))
      expect(statuses.filter((s) => s === 429)).to.have.length(1)
      const keys: string[] = await client.keys(`ratelimit:${prefix}:*`)
      expect(keys).to.have.length(1)
      expect(await client.ttl(keys[0])).to.be.within(1, 62)
    })

    it('#19: auth per IP and oauth-client surfaces count in Redis across instances, surfaces stay apart', async () => {
      const cache: ICacheService = {
        get: async () => null,
        set: async () => {},
        delete: async (key) => { await client.del(key) },
        increment: async (key, options) => {
          const n = await client.incr(key)
          if (options?.ttl !== undefined) await client.expire(key, options.ttl)
          return n
        },
        disconnect: async () => {},
        isConnected: () => true,
      }
      useRateLimitCache(cache)
      const log = loggerStub as unknown as Logger
      const ip = `10.${String(Date.now() % 250)}.0.1`
      const hitIp = (limiter: any): Promise<number> =>
        new Promise((resolve) => {
          const res = createMockResponse()
          res.json.callsFake(() => { resolve(res.statusCode); return res })
          limiter(createMockRequest({ ip, path: '/initAuth', method: 'POST' }), res, () => resolve(200))
        })
      const authA = createAuthRateLimiter(log, 2)
      const authB = createAuthRateLimiter(log, 2)
      expect([await hitIp(authA), await hitIp(authB), await hitIp(authA)]).to.deep.equal([200, 200, 429])
      const userId = `u-${String(Date.now())}`
      const pat = createOAuthClientRateLimiter(log, 1, 'pat')
      const clients = createOAuthClientRateLimiter(log, 1, 'clients')
      expect(await hit(pat, userId)).to.equal(200)
      expect(await hit(clients, userId)).to.equal(200)
      expect(await hit(createOAuthClientRateLimiter(log, 1, 'pat'), userId)).to.equal(429)
      expect(await client.keys(`ratelimit:oauth-client:pat:user:${userId}:*`)).to.have.length(1)
      expect(await client.keys(`ratelimit:oauth-client:clients:user:${userId}:*`)).to.have.length(1)
      expect((await client.keys(`ratelimit:auth:ip:${ip}:*`)).length).to.equal(1)
    })
  })

  // -----------------------------------------------------------------------
  // Python services' caller-role lookups
  // -----------------------------------------------------------------------
  describe('caller-role lookups from the Python services', () => {
    const SCOPED_SECRET = 'scoped-secret-for-tests'
    const PYTHON_IP = '10.9.0.2'
    const LIMIT = 1000

    function serviceToken(
      scopes: string[] = [TokenScopes.CALLER_ROLE],
      secret = SCOPED_SECRET,
    ): string {
      return jwt.sign({ scopes }, secret, { expiresIn: '5m' })
    }

    // Plain objects rather than sinon stubs: these tests send over a thousand requests.
    function lookup(token?: string, overrides: Record<string, any> = {}): any {
      return {
        method: 'GET',
        path: CALLER_ROLE_LOOKUP_PATH,
        ip: PYTHON_IP,
        socket: { remoteAddress: PYTHON_IP },
        app: { enabled: () => false },
        get: () => undefined,
        headers: {
          authorization: 'Bearer some-users-session',
          ...(token ? { [SERVICE_AUTHORIZATION_HEADER]: `Bearer ${token}` } : {}),
        },
        ...overrides,
      }
    }

    // Resolves with 200 when the limiter lets the request through, else its status.
    function send(limiter: any, req: any): Promise<number> {
      return new Promise((resolve, reject) => {
        const headers: Record<string, unknown> = {}
        const res: any = {
          statusCode: 200,
          headersSent: false,
          setHeader: (key: string, value: unknown) => { headers[key] = value; return res },
          getHeader: (key: string) => headers[key],
          header: (key: string, value: unknown) => { headers[key] = value; return res },
          set: (key: string, value: unknown) => { headers[key] = value; return res },
          status: (code: number) => { res.statusCode = code; return res },
          json: () => { resolve(res.statusCode); return res },
          send: () => { resolve(res.statusCode); return res },
        }
        Promise.resolve(limiter(req, res, () => resolve(200))).catch(reject)
      })
    }

    async function statuses(limiter: any, requests: any[]): Promise<number[]> {
      const seen: number[] = []
      for (const req of requests) {
        seen.push(await send(limiter, req))
      }
      return seen
    }

    it('lets a burst of more than 1,000 a minute through from one address', async () => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, LIMIT, SCOPED_SECRET)
      const token = serviceToken()

      const seen = await statuses(
        limiter,
        Array.from({ length: LIMIT + 200 }, () => lookup(token)),
      )

      expect(seen.filter((status) => status === 429)).to.have.lengthOf(0)
    })

    it('still limits an ordinary client at the same address', async () => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, LIMIT, SCOPED_SECRET)

      const seen = await statuses(
        limiter,
        Array.from({ length: LIMIT + 1 }, () => lookup()),
      )

      expect(seen.slice(0, LIMIT).every((status) => status === 200)).to.be.true
      expect(seen[LIMIT]).to.equal(429)
    })

    it('counts a lookup whose service token is forged, mis-scoped, expired or aimed elsewhere', async () => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1, SCOPED_SECRET)
      const expired = jwt.sign(
        { scopes: [TokenScopes.CALLER_ROLE], exp: Math.floor(Date.now() / 1000) - 60 },
        SCOPED_SECRET,
      )
      const refused = [
        lookup(serviceToken([TokenScopes.CALLER_ROLE], 'not-the-server-secret'), { ip: '10.9.1.1' }),
        lookup(serviceToken([TokenScopes.FETCH_CONFIG]), { ip: '10.9.1.2' }),
        lookup(expired, { ip: '10.9.1.3' }),
        lookup(serviceToken(), { ip: '10.9.1.4', path: '/api/v1/users' }),
        lookup(serviceToken(), { ip: '10.9.1.5', method: 'POST' }),
      ]

      for (const req of refused) {
        // The first request from each address fits the allowance of one; the second must not.
        expect(await send(limiter, req)).to.equal(200)
        expect(await send(limiter, { ...req })).to.equal(429)
      }
    })

    it('exempts nothing when no scoped secret is configured', async () => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 1)
      const token = serviceToken()

      expect(await statuses(limiter, [lookup(token), lookup(token)])).to.deep.equal([200, 429])
    })
  })
})
