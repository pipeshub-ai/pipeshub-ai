import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createGlobalRateLimiter, createOAuthClientRateLimiter } from '../../../src/libs/middlewares/rate-limit.middleware'
import { Logger } from '../../../src/libs/services/logger.service'

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
  // Client IP extraction
  // -----------------------------------------------------------------------
  describe('Client IP extraction', () => {
    it('should use X-Forwarded-For header when present', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 100)
      const req = createMockRequest({
        headers: { 'x-forwarded-for': '203.0.113.50, 70.41.3.18' },
        ip: '10.0.2.1',
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.called).to.be.true
        done()
      })

      limiter(req, res, next)
    })

    it('should use X-Real-IP header when X-Forwarded-For is not present', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 100)
      const req = createMockRequest({
        headers: { 'x-real-ip': '203.0.113.60' },
        ip: '10.0.2.2',
      })
      const res = createMockResponse()
      const next = createMockNext()

      next.callsFake(() => {
        expect(next.called).to.be.true
        done()
      })

      limiter(req, res, next)
    })

    it('should fall back to req.ip when no forwarding headers present', (done) => {
      const limiter = createGlobalRateLimiter(loggerStub as unknown as Logger, 100)
      const req = createMockRequest({
        ip: '192.168.0.100',
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
})
