import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import { AddressInfo } from 'net'
import { createAuthRateLimiter, createGlobalRateLimiter, createOAuthClientRateLimiter, createSkillsImportRateLimiter } from '../../../src/libs/middlewares/rate-limit.middleware'
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
  })
})
