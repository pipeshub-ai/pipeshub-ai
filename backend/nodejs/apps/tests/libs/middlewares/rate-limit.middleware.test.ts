import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import express from 'express'
import { AddressInfo } from 'net'
import {
  CALLER_ROLE_LOOKUP_PATH,
  SERVICE_AUTHORIZATION_HEADER,
  createAuthIpRateLimiter,
  createAuthRateLimiter,
  createGlobalRateLimiter,
  createOAuthClientRateLimiter,
  createSkillsImportRateLimiter,
  createSlackEventsRateLimiter,
} from '../../../src/libs/middlewares/rate-limit.middleware'
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum'
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

// Resolves with 200 when the limiter lets the request through, else its status.
function attempt(limiter: any, overrides: Record<string, any>): Promise<number> {
  return new Promise((resolve) => {
    const res = createMockResponse()
    res.json.callsFake(() => { resolve(res.statusCode); return res })
    limiter(createMockRequest({ method: 'POST', ...overrides }), res, () => resolve(200))
  })
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
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 10)
      expect(limiter).to.be.a('function')
    })

    it('should allow requests within the rate limit', (done) => {
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 10)
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
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 10)
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
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 1)
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
      const limiter = createOAuthClientRateLimiter(loggerStub as unknown as Logger, 1)
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

    it('should give each account behind one IP its own bucket', async () => {
      const limiter = createAuthRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.7.2'

      expect(await attempt(limiter, { ip, body: { email: 'alice@example.com' } })).to.equal(200)
      expect(await attempt(limiter, { ip, body: { email: 'bob@example.com' } })).to.equal(200)
      expect(await attempt(limiter, { ip, body: { email: ' Alice@Example.com ' } })).to.equal(429)
    })

    it('should still share one bucket per IP for requests that name no account', async () => {
      const limiter = createAuthRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.7.3'

      expect(await attempt(limiter, { ip, body: {} })).to.equal(200)
      expect(await attempt(limiter, { ip, body: { email: 42 } })).to.equal(429)
    })

    it('should key on the session email so rotating body.email gets no fresh bucket', async () => {
      const limiter = createAuthRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.7.4'
      const sessionInfo = { email: 'target@example.com' }

      expect(await attempt(limiter, { ip, sessionInfo, body: { email: 'a@example.com' } })).to.equal(200)
      expect(await attempt(limiter, { ip, sessionInfo, body: { email: 'b@example.com' } })).to.equal(429)
    })

    it('should count a signed-in user on their own key whatever the IP', async () => {
      const limiter = createAuthRateLimiter(loggerStub as unknown as Logger, 1)
      const user = { userId: 'auth-rate-user' }

      expect(await attempt(limiter, { ip: '10.0.7.5', user })).to.equal(200)
      expect(await attempt(limiter, { ip: '10.0.7.5', user: { userId: 'auth-rate-other' } })).to.equal(200)
      expect(await attempt(limiter, { ip: '10.0.7.6', user })).to.equal(429)
    })

    it('should keep email addresses out of the 429 log line', async () => {
      const limiter = createAuthRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.7.7'
      const body = { email: 'private@example.com' }

      await attempt(limiter, { ip, body })
      expect(await attempt(limiter, { ip, body })).to.equal(429)
      const logged = JSON.stringify(loggerStub.warn.firstCall.args)
      expect(logged).to.include(`auth:ip:${ip}:acct:`)
      expect(logged).to.not.include('private@example.com')
    })
  })

  // -----------------------------------------------------------------------
  // createAuthIpRateLimiter
  // -----------------------------------------------------------------------
  describe('createAuthIpRateLimiter', () => {
    it('should cap one IP across accounts at six times the per-account limit', async () => {
      const limiter = createAuthIpRateLimiter(loggerStub as unknown as Logger, 1)
      const ip = '10.0.8.1'

      for (let i = 0; i < 6; i++) {
        expect(await attempt(limiter, { ip, body: { email: `user${i}@example.com` } })).to.equal(200)
      }
      expect(await attempt(limiter, { ip, body: { email: 'user6@example.com' } })).to.equal(429)
      expect(await attempt(limiter, { ip: '10.0.8.2', body: {} })).to.equal(200)
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

  describe('createSlackEventsRateLimiter', () => {
    // Real Express + socket: skipSuccessfulRequests reads the status the
    // response finishes with, which a hand-built res does not exercise.
    async function deliver(limit: number, statuses: number[]): Promise<number[]> {
      const app = express()
      app.use(createSlackEventsRateLimiter(loggerStub as unknown as Logger, limit))
      app.post('/slack/events', (req, res) => { res.status(Number(req.query.status)).end() })
      const server = app.listen(0)
      await new Promise((resolve) => server.once('listening', resolve))
      const { port } = server.address() as AddressInfo
      try {
        const results: number[] = []
        for (const status of statuses) {
          const response = await fetch(`http://127.0.0.1:${port}/slack/events?status=${status}`, { method: 'POST' })
          results.push(response.status)
        }
        return results
      } finally {
        server.close()
      }
    }

    it('should never throttle deliveries the bot accepted', async () => {
      const results = await deliver(2, [200, 200, 200, 200, 200, 200])
      expect(results).to.deep.equal([200, 200, 200, 200, 200, 200])
    })

    it('should return 429 once failed deliveries exceed the limit', async () => {
      const results = await deliver(2, [401, 401, 401])
      expect(results).to.deep.equal([401, 401, 429])
    })

    it('should not let accepted deliveries use up the allowance for failures', async () => {
      const results = await deliver(1, [200, 200, 200, 401, 401])
      expect(results).to.deep.equal([200, 200, 200, 401, 429])
    })
  })
})
