import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { EventEmitter } from 'events'
import { Container } from 'inversify'
import { metricsMiddleware } from '../../../src/libs/middlewares/prometheus.middleware'
import { PrometheusService } from '../../../src/libs/services/prometheus/prometheus.service'
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
    get: sinon.stub(),
    user: undefined,
    context: undefined,
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = Object.assign(new EventEmitter(), {
    status: sinon.stub(),
    json: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
    statusCode: 200,
  })
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

describe('Prometheus Metrics Middleware', () => {
  let container: Container
  let mockPrometheusService: sinon.SinonStubbedInstance<PrometheusService>
  let loggerStub: sinon.SinonStubbedInstance<Logger>

  beforeEach(() => {
    // The prometheus middleware captures Logger.getInstance() at module level.
    // Stub methods on the singleton directly.
    const loggerInstance = Logger.getInstance()
    loggerStub = {
      error: sinon.stub(loggerInstance, 'error'),
      warn: sinon.stub(loggerInstance, 'warn'),
      debug: sinon.stub(loggerInstance, 'debug'),
      info: sinon.stub(loggerInstance, 'info'),
    } as any

    mockPrometheusService = sinon.createStubInstance(PrometheusService)
    container = new Container()
    container.bind(PrometheusService).toConstantValue(mockPrometheusService as unknown as PrometheusService)
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // Basic behavior
  // -----------------------------------------------------------------------
  describe('Basic behavior', () => {
    it('should call next() immediately', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should register a finish event listener on the response', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(res.listenerCount('finish')).to.equal(1)
    })
  })

  // -----------------------------------------------------------------------
  // Success recording (2xx, 3xx)
  // -----------------------------------------------------------------------
  describe('Success recording (status 200-399)', () => {
    it('should record success activity for 200 status', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest({
        user: { userId: 'user1', orgId: 'org1', email: 'user@test.com', fullName: 'Test User' },
        context: { requestId: 'req-123' },
      })
      const res = createMockResponse()
      res.statusCode = 200
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[0]).to.equal('success')
      expect(args[1]).to.equal('user1')
      expect(args[2]).to.equal('org1')
      expect(args[3]).to.equal('user@test.com')
      expect(args[4]).to.equal('Test User')
      expect(args[5]).to.equal('req-123')
      expect(args[6]).to.equal('GET')
      expect(args[7]).to.equal('/test')
      expect(args[9]).to.equal(200)
    })

    it('should record success activity for 201 status', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest({
        method: 'POST',
        path: '/api/users',
        user: { userId: 'user2', orgId: 'org2', email: 'u2@test.com', fullName: 'U2' },
        context: { requestId: 'req-456' },
      })
      const res = createMockResponse()
      res.statusCode = 201
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[0]).to.equal('success')
      expect(args[9]).to.equal(201)
    })

    it('should record success activity for 302 redirect', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest({
        user: { userId: 'user1', orgId: 'org1' },
      })
      const res = createMockResponse()
      res.statusCode = 302
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[0]).to.equal('success')
    })
  })

  // -----------------------------------------------------------------------
  // Error recording (4xx, 5xx)
  // -----------------------------------------------------------------------
  describe('Error recording (status >= 400)', () => {
    it('should record error activity for 400 status', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest({
        user: { userId: 'user1', orgId: 'org1', email: 'user@test.com', fullName: 'User' },
        context: { requestId: 'req-err1' },
      })
      const res = createMockResponse()
      res.statusCode = 400
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[0]).to.equal('error')
      expect(args[9]).to.equal(400)
    })

    it('should record error activity for 404 status', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest({
        user: { userId: 'user1', orgId: 'org1' },
      })
      const res = createMockResponse()
      res.statusCode = 404
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[0]).to.equal('error')
    })

    it('should record error activity for 500 status', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest({
        user: { userId: 'user1', orgId: 'org1', email: 'u@t.com', fullName: 'U' },
        context: { requestId: 'req-500' },
      })
      const res = createMockResponse()
      res.statusCode = 500
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[0]).to.equal('error')
      expect(args[9]).to.equal(500)
    })
  })

  // -----------------------------------------------------------------------
  // Default values for anonymous/missing fields
  // -----------------------------------------------------------------------
  describe('Default values', () => {
    it('should use "anonymous" for userId when user is not set', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest()
      const res = createMockResponse()
      res.statusCode = 200
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[1]).to.equal('anonymous')
      expect(args[2]).to.equal('unknown')
      expect(args[3]).to.equal('unknown')
      expect(args[4]).to.equal('unknown')
    })

    it('should use undefined for requestId when context is not set', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest()
      const res = createMockResponse()
      res.statusCode = 200
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.calledOnce).to.be.true
      const args = mockPrometheusService.recordActivity.firstCall.args
      expect(args[5]).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // No recording for 1xx status codes (informational)
  // -----------------------------------------------------------------------
  describe('No recording for 1xx status', () => {
    it('should not record activity for 100 status (informational)', () => {
      const middleware = metricsMiddleware(container)
      const req = createMockRequest()
      const res = createMockResponse()
      res.statusCode = 100
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      expect(mockPrometheusService.recordActivity.called).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Context serialization
  // -----------------------------------------------------------------------
  describe('Context serialization', () => {
    it('should serialize request context as JSON string', () => {
      const context = {
        requestId: 'req-ctx-test',
        timestamp: 1234567890,
        headers: { userAgent: 'test' },
        meta: { path: '/test', method: 'GET', protocol: 'http', originalUrl: '/test' },
      }

      const middleware = metricsMiddleware(container)
      const req = createMockRequest({
        user: { userId: 'user1', orgId: 'org1', email: 'e', fullName: 'n' },
        context,
      })
      const res = createMockResponse()
      res.statusCode = 200
      const next = createMockNext()

      middleware(req, res, next)
      res.emit('finish')

      const args = mockPrometheusService.recordActivity.firstCall.args
      const contextArg = args[8]
      expect(contextArg).to.be.a('string')
      const parsed = JSON.parse(contextArg)
      expect(parsed.requestId).to.equal('req-ctx-test')
    })
  })
})
