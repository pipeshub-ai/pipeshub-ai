import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  const getStub = sinon.stub()
  getStub.withArgs('user-agent').returns('test-agent')
  getStub.withArgs('X-Correlation-ID').returns(undefined)
  getStub.withArgs('X-Request-ID').returns(undefined)
  getStub.withArgs('origin').returns(undefined)
  getStub.withArgs('referer').returns(undefined)
  getStub.withArgs('accept-language').returns(undefined)

  return {
    headers: {},
    body: {},
    params: {},
    query: {},
    path: '/test',
    method: 'GET',
    ip: '127.0.0.1',
    protocol: 'http',
    originalUrl: '/test?foo=bar',
    get: getStub,
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.send.returns(res)
  res.setHeader.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Request Context Middleware', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // Basic context creation
  // -----------------------------------------------------------------------
  describe('Context creation', () => {
    it('should attach context to req object', () => {
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context).to.exist
      expect(next.calledOnce).to.be.true
    })

    it('should generate a requestId when none is provided', () => {
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.requestId).to.be.a('string')
      expect(req.context.requestId.length).to.be.greaterThan(0)
      // Generated format: timestamp-hex
      expect(req.context.requestId).to.match(/^\d+-[a-f0-9]+$/)
    })

    it('should use existing X-Request-ID header if provided', () => {
      const getStub = sinon.stub()
      getStub.withArgs('user-agent').returns('test-agent')
      getStub.withArgs('X-Correlation-ID').returns(undefined)
      getStub.withArgs('X-Request-ID').returns('custom-request-id')
      getStub.withArgs('origin').returns(undefined)
      getStub.withArgs('referer').returns(undefined)
      getStub.withArgs('accept-language').returns(undefined)

      const req = createMockRequest({ get: getStub })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.requestId).to.equal('custom-request-id')
    })

    it('should include correlationId from X-Correlation-ID header', () => {
      const getStub = sinon.stub()
      getStub.withArgs('user-agent').returns('test-agent')
      getStub.withArgs('X-Correlation-ID').returns('correlation-123')
      getStub.withArgs('X-Request-ID').returns(undefined)
      getStub.withArgs('origin').returns(undefined)
      getStub.withArgs('referer').returns(undefined)
      getStub.withArgs('accept-language').returns(undefined)

      const req = createMockRequest({ get: getStub })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.correlationId).to.equal('correlation-123')
      expect(req.context.headers.correlationId).to.equal('correlation-123')
    })

    it('should include timestamp as a number', () => {
      const before = Date.now()
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)
      const after = Date.now()

      expect(req.context.timestamp).to.be.a('number')
      expect(req.context.timestamp).to.be.at.least(before)
      expect(req.context.timestamp).to.be.at.most(after)
    })
  })

  // -----------------------------------------------------------------------
  // Headers context
  // -----------------------------------------------------------------------
  describe('Headers context', () => {
    it('should capture user-agent header', () => {
      const getStub = sinon.stub()
      getStub.withArgs('user-agent').returns('Mozilla/5.0 Test')
      getStub.withArgs('X-Correlation-ID').returns(undefined)
      getStub.withArgs('X-Request-ID').returns(undefined)
      getStub.withArgs('origin').returns(undefined)
      getStub.withArgs('referer').returns(undefined)
      getStub.withArgs('accept-language').returns(undefined)

      const req = createMockRequest({ get: getStub })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.headers.userAgent).to.equal('Mozilla/5.0 Test')
    })

    it('should default user-agent to "unknown" when not present', () => {
      const getStub = sinon.stub()
      getStub.withArgs('user-agent').returns(undefined)
      getStub.withArgs('X-Correlation-ID').returns(undefined)
      getStub.withArgs('X-Request-ID').returns(undefined)
      getStub.withArgs('origin').returns(undefined)
      getStub.withArgs('referer').returns(undefined)
      getStub.withArgs('accept-language').returns(undefined)

      const req = createMockRequest({ get: getStub })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.headers.userAgent).to.equal('unknown')
    })

    it('should capture clientIp from req.ip', () => {
      const req = createMockRequest({ ip: '192.168.1.1' })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.headers.clientIp).to.equal('192.168.1.1')
    })

    it('should capture origin header', () => {
      const getStub = sinon.stub()
      getStub.withArgs('user-agent').returns('test-agent')
      getStub.withArgs('X-Correlation-ID').returns(undefined)
      getStub.withArgs('X-Request-ID').returns(undefined)
      getStub.withArgs('origin').returns('https://example.com')
      getStub.withArgs('referer').returns(undefined)
      getStub.withArgs('accept-language').returns(undefined)

      const req = createMockRequest({ get: getStub })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.headers.origin).to.equal('https://example.com')
    })

    it('should capture referer header', () => {
      const getStub = sinon.stub()
      getStub.withArgs('user-agent').returns('test-agent')
      getStub.withArgs('X-Correlation-ID').returns(undefined)
      getStub.withArgs('X-Request-ID').returns(undefined)
      getStub.withArgs('origin').returns(undefined)
      getStub.withArgs('referer').returns('https://example.com/page')
      getStub.withArgs('accept-language').returns(undefined)

      const req = createMockRequest({ get: getStub })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.headers.referer).to.equal('https://example.com/page')
    })

    it('should capture accept-language header', () => {
      const getStub = sinon.stub()
      getStub.withArgs('user-agent').returns('test-agent')
      getStub.withArgs('X-Correlation-ID').returns(undefined)
      getStub.withArgs('X-Request-ID').returns(undefined)
      getStub.withArgs('origin').returns(undefined)
      getStub.withArgs('referer').returns(undefined)
      getStub.withArgs('accept-language').returns('en-US,en;q=0.9')

      const req = createMockRequest({ get: getStub })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.headers.language).to.equal('en-US,en;q=0.9')
    })
  })

  // -----------------------------------------------------------------------
  // Meta context
  // -----------------------------------------------------------------------
  describe('Meta context', () => {
    it('should capture path, method, protocol, and originalUrl', () => {
      const req = createMockRequest({
        path: '/api/v1/users',
        method: 'POST',
        protocol: 'https',
        originalUrl: '/api/v1/users?active=true',
      })
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(req.context.meta.path).to.equal('/api/v1/users')
      expect(req.context.meta.method).to.equal('POST')
      expect(req.context.meta.protocol).to.equal('https')
      expect(req.context.meta.originalUrl).to.equal('/api/v1/users?active=true')
    })
  })

  // -----------------------------------------------------------------------
  // X-Request-ID header in response
  // -----------------------------------------------------------------------
  describe('X-Request-ID response header', () => {
    it('should set X-Request-ID in response when NEED_REQUEST_ID_HEADER is true', () => {
      const originalEnv = process.env.NEED_REQUEST_ID_HEADER
      process.env.NEED_REQUEST_ID_HEADER = 'true'

      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(res.setHeader.calledWith('X-Request-ID', req.context.requestId)).to.be.true

      process.env.NEED_REQUEST_ID_HEADER = originalEnv
    })

    it('should NOT set X-Request-ID in response when NEED_REQUEST_ID_HEADER is not true', () => {
      const originalEnv = process.env.NEED_REQUEST_ID_HEADER
      delete process.env.NEED_REQUEST_ID_HEADER

      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req, res, next)

      expect(res.setHeader.calledWith('X-Request-ID', sinon.match.any)).to.be.false

      process.env.NEED_REQUEST_ID_HEADER = originalEnv
    })
  })

  // -----------------------------------------------------------------------
  // Unique requestId per call
  // -----------------------------------------------------------------------
  describe('Request ID uniqueness', () => {
    it('should generate unique requestIds for different requests', () => {
      const req1 = createMockRequest()
      const req2 = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      requestContextMiddleware(req1, res, next)
      requestContextMiddleware(req2, res, next)

      expect(req1.context.requestId).to.not.equal(req2.context.requestId)
    })
  })
})
