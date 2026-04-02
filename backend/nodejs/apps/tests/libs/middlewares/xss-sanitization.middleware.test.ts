import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { xssSanitizationMiddleware } from '../../../src/libs/middlewares/xss-sanitization.middleware'
import { BadRequestError } from '../../../src/libs/errors/http.errors'
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

describe('XSS Sanitization Middleware', () => {
  let loggerInstance: Logger

  beforeEach(() => {
    loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // Happy path - clean data passes through
  // -----------------------------------------------------------------------
  describe('Clean data', () => {
    it('should call next() with clean body data', () => {
      const req = createMockRequest({
        body: { name: 'Alice', age: 30 },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.name).to.equal('Alice')
      expect(req.body.age).to.equal(30)
    })

    it('should call next() with clean query data', () => {
      const req = createMockRequest({
        query: { search: 'hello world', page: '1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next() with clean params data', () => {
      const req = createMockRequest({
        params: { id: '123' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next() when body is empty', () => {
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should preserve numeric and boolean values', () => {
      const req = createMockRequest({
        body: { count: 42, active: true, ratio: 3.14 },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.count).to.equal(42)
      expect(req.body.active).to.equal(true)
      expect(req.body.ratio).to.equal(3.14)
    })

    it('should preserve null and undefined values', () => {
      const req = createMockRequest({
        body: { a: null, b: undefined },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.a).to.be.null
      expect(req.body.b).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // XSS detection - body
  // -----------------------------------------------------------------------
  describe('XSS detection in body', () => {
    it('should reject body with <script> tag', () => {
      const req = createMockRequest({
        body: { name: '<script>alert("xss")</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject body with <img onerror> payload', () => {
      const req = createMockRequest({
        body: { avatar: '<img src=x onerror="alert(1)">' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject body with javascript: protocol', () => {
      const req = createMockRequest({
        body: { link: 'javascript:alert(1)' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject body with <iframe> tag', () => {
      const req = createMockRequest({
        body: { content: '<iframe src="https://evil.com"></iframe>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject body with event handler attributes', () => {
      const req = createMockRequest({
        body: { bio: 'Hello <div onmouseover="steal()">' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject nested XSS in body objects', () => {
      const req = createMockRequest({
        body: { user: { profile: { bio: '<script>steal()</script>' } } },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject XSS in arrays within body', () => {
      const req = createMockRequest({
        body: { tags: ['safe', '<script>evil()</script>'] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject encoded script tags', () => {
      const req = createMockRequest({
        body: { name: '%3cscript%3ealert(1)%3c/script%3e' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  // -----------------------------------------------------------------------
  // XSS detection - query
  // -----------------------------------------------------------------------
  describe('XSS detection in query', () => {
    it('should reject query with XSS payload', () => {
      const req = createMockRequest({
        query: { search: '<script>alert("xss")</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  // -----------------------------------------------------------------------
  // XSS detection - params
  // -----------------------------------------------------------------------
  describe('XSS detection in params', () => {
    it('should reject params with XSS payload', () => {
      const req = createMockRequest({
        params: { id: '<svg onload=alert(1)>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  // -----------------------------------------------------------------------
  // Bypass paths
  // -----------------------------------------------------------------------
  describe('Bypass paths', () => {
    it('should skip validation for POST to /api/v1/agents/ paths', () => {
      const req = createMockRequest({
        path: '/api/v1/agents/create',
        method: 'POST',
        body: { content: '<script>alert(1)</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should skip validation for PUT to /api/v1/agents/ paths', () => {
      const req = createMockRequest({
        path: '/api/v1/agents/update',
        method: 'PUT',
        body: { content: '<script>alert(1)</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should skip validation for POST to /api/v1/conversations/ paths', () => {
      const req = createMockRequest({
        path: '/api/v1/conversations/new',
        method: 'POST',
        body: { message: '<script>alert(1)</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should NOT skip validation for GET to /api/v1/agents/ paths', () => {
      const req = createMockRequest({
        path: '/api/v1/agents/',
        method: 'GET',
        query: { name: '<script>alert(1)</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  // -----------------------------------------------------------------------
  // Buffer body handling
  // -----------------------------------------------------------------------
  describe('Buffer body handling', () => {
    it('should skip validation when body is a Buffer', () => {
      const req = createMockRequest({
        body: Buffer.from('binary content'),
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // Multer file object handling
  // -----------------------------------------------------------------------
  describe('Multer file object handling', () => {
    it('should skip validation for objects that look like multer file uploads', () => {
      const req = createMockRequest({
        body: {
          file: {
            fieldname: 'file',
            originalname: 'test.txt',
            buffer: Buffer.from('file content'),
            mimetype: 'text/plain',
            size: 12,
          },
          name: 'safe string',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // Special types
  // -----------------------------------------------------------------------
  describe('Special types', () => {
    it('should preserve Date objects', () => {
      const date = new Date()
      const req = createMockRequest({
        body: { created: date },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle array body (skip validation since it checks for non-array objects)', () => {
      const req = createMockRequest({
        body: ['item1', 'item2'],
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should preserve Buffer objects in body', () => {
      const req = createMockRequest({
        body: { data: 'text', buf: Buffer.from('binary') },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should preserve RegExp objects in body', () => {
      const req = createMockRequest({
        body: { pattern: /test/gi, name: 'safe' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should preserve Error objects in body', () => {
      const req = createMockRequest({
        body: { err: new Error('test error'), name: 'safe' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle numeric body value (non-object)', () => {
      const req = createMockRequest({
        body: 42,
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle string body value (non-object)', () => {
      const req = createMockRequest({
        body: 'just a string',
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should preserve multer file object in sanitizeValue (with buffer)', () => {
      const req = createMockRequest({
        body: {
          file: {
            fieldname: 'avatar',
            originalname: 'photo.jpg',
            buffer: Buffer.from('file data'),
            mimetype: 'image/jpeg',
            size: 1024,
          },
          description: 'profile photo',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      // The file object should be preserved
      expect(req.body.file).to.have.property('fieldname', 'avatar')
      expect(req.body.file.buffer).to.be.instanceOf(Buffer)
    })

    it('should handle arrays in body objects', () => {
      const req = createMockRequest({
        body: { items: ['safe', 'also safe'], count: 2 },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle nested arrays in query', () => {
      const req = createMockRequest({
        query: { tags: ['safe1', 'safe2'] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle empty string values', () => {
      const req = createMockRequest({
        body: { name: '' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // Non-breaking on unexpected errors
  // -----------------------------------------------------------------------
  describe('Non-breaking behavior', () => {
    it('should continue on unexpected non-BadRequestError errors during sanitization', () => {
      // Create a pathological object whose property access throws
      const req = createMockRequest({
        body: {},
        query: {},
        params: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      // Even with normal data, middleware should never throw
      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should log and continue when a non-BadRequestError is thrown during processing', () => {
      // Create a body with a getter that throws a TypeError (not BadRequestError)
      const body: any = {}
      Object.defineProperty(body, 'bad', {
        get() { throw new TypeError('unexpected getter error') },
        enumerable: true,
      })
      body.safe = 'value'

      const req = createMockRequest({ body })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      // Should have called next without error (non-breaking)
      expect(next.calledOnce).to.be.true
      // The error was non-BadRequestError, so next() is called without args
      // (the error was caught and logged)
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle array query param', () => {
      const req = createMockRequest({
        query: ['not', 'an', 'object'],
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle array params', () => {
      const req = createMockRequest({
        params: ['not', 'an', 'object'],
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // SVG and embed tags
  // -----------------------------------------------------------------------
  describe('SVG and embed XSS vectors', () => {
    it('should reject <svg> tag in body', () => {
      const req = createMockRequest({
        body: { content: '<svg onload="alert(1)">' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject <embed> tag in body', () => {
      const req = createMockRequest({
        body: { content: '<embed src="data:text/html,<script>alert(1)</script>">' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should reject <object> tag in body', () => {
      const req = createMockRequest({
        body: { content: '<object data="javascript:alert(1)"></object>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  // -----------------------------------------------------------------------
  // data: protocol
  // -----------------------------------------------------------------------
  describe('data: protocol XSS', () => {
    it('should reject data:text/html payloads', () => {
      const req = createMockRequest({
        body: { link: 'data: text/html,<script>alert(1)</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })
})
