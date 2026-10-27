import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { xssSanitizationMiddleware } from '../../../src/libs/middlewares/xss-sanitization.middleware'
import { BadRequestError } from '../../../src/libs/errors/http.errors'
import { Logger } from '../../../src/libs/services/logger.service'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import express from 'express'
import http from 'http'
import type { AddressInfo } from 'net'

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

    it('passes an API toolset spec and credentials through unchanged', () => {
      const spec = 'info:\n  description: >\n    Tickets & <b>comments</b>\n'
      const res = createMockResponse()
      for (const [method, path, body] of [
        ['POST', '/api/v1/api-toolsets/validate', { spec }],
        ['POST', '/api/v1/api-toolsets/instances', { name: 'Acme', spec }],
        ['PUT', '/api/v1/api-toolsets/instances/abc', { name: 'Acme', spec }],
        ['PUT', '/api/v1/api-toolsets/instances/abc/credentials', { apiKey: 'k<&>y' }],
      ] as const) {
        const req = createMockRequest({ path, method, body: { ...body } })
        const next = createMockNext()
        xssSanitizationMiddleware(req, res, next)
        expect(next.firstCall.args, `${method} ${path}`).to.have.length(0)
        expect(req.body, `${method} ${path}`).to.deep.equal(body)
      }
    })

    it('should skip validation for POST to /api/v1/skills/import/finalize', () => {
      const req = createMockRequest({
        path: '/api/v1/skills/import/finalize',
        method: 'POST',
        body: {
          content: '<html><body><script>alert(1)</script></body></html>',
          resources: [{ path: 'scripts/preview.js', content: '<div onclick="x()">hi</div>' }],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.content).to.include('<script>')
    })

    it('should skip validation for POST to /api/v1/skills (create)', () => {
      const req = createMockRequest({
        path: '/api/v1/skills',
        method: 'POST',
        body: { name: 'pptx', content: 'Use <html> slides' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should skip validation for PUT and PATCH to /api/v1/skills/:name', () => {
      const res = createMockResponse()

      const putReq = createMockRequest({
        path: '/api/v1/skills/pptx',
        method: 'PUT',
        body: { content: '<svg></svg>' },
      })
      const putNext = createMockNext()
      xssSanitizationMiddleware(putReq, res, putNext)
      expect(putNext.calledOnce).to.be.true
      expect(putNext.firstCall.args).to.have.length(0)

      const patchReq = createMockRequest({
        path: '/api/v1/skills/pptx/body',
        method: 'PATCH',
        body: { content: '<iframe src="x"></iframe>' },
      })
      const patchNext = createMockNext()
      xssSanitizationMiddleware(patchReq, res, patchNext)
      expect(patchNext.calledOnce).to.be.true
      expect(patchNext.firstCall.args).to.have.length(0)
    })

    it('should NOT skip validation for GET to /api/v1/skills', () => {
      const req = createMockRequest({
        path: '/api/v1/skills',
        method: 'GET',
        query: { q: '<script>alert(1)</script>' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      xssSanitizationMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
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
  // -----------------------------------------------------------------------
  // The rejection says which field was refused and why, so a caller whose
  // text merely contains something like `<id>` can tell what to change.
  // -----------------------------------------------------------------------
  describe('Rejection message', () => {
    let server: http.Server
    let baseUrl: string

    before(async () => {
      const app = express()
      app.use(express.json())
      app.use(xssSanitizationMiddleware)
      app.all('*', (_req, res) => {
        res.status(200).json({ ok: true })
      })
      app.use(ErrorMiddleware.handleError())
      server = await new Promise<http.Server>((resolve) => {
        const s = app.listen(0, '127.0.0.1', () => resolve(s))
      })
      baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })

    after(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    })

    const post = async (path: string, body: unknown) => {
      const resp = await fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      return { status: resp.status, body: (await resp.json()) as any }
    }

    it('names the body field and explains that <id> counts as a tag', async () => {
      const { status, body } = await post('/api/v1/knowledgeBase', {
        name: 'cite [Summary of <id>]',
      })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"name"')
      expect(body.error.message).to.include('such as <id>')
    })

    it('names the nested field of an MCP tool call', async () => {
      const { status, body } = await post('/mcp', {
        jsonrpc: '2.0',
        id: 1,
        method: 'tools/call',
        params: {
          name: 'pipeshub_chat',
          arguments: { query: 'Any bugs?', conversationId: '<id>' },
        },
      })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"params.arguments.conversationId"')
    })

    it('names a query-string parameter as one', async () => {
      const resp = await fetch(
        `${baseUrl}/api/v1/users?search=${encodeURIComponent('<b>x</b>')}`,
      )
      const body = (await resp.json()) as any

      expect(resp.status).to.equal(400)
      expect(body.error.message).to.include('query parameter "search"')
    })

    it('names a route parameter as a URL parameter', () => {
      const req = createMockRequest({ params: { id: '<svg onload=alert(1)>' } })
      const next = createMockNext()

      xssSanitizationMiddleware(req, createMockResponse(), next)

      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
      expect(error.message).to.include('URL parameter "id"')
    })

    it('names the field but gives no <id> hint when nothing is tag-shaped', async () => {
      const { status, body } = await post('/api/v1/search', {
        url: 'javascript:alert(1)',
      })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"url"')
      expect(body.error.message).not.to.include('<id>')
      expect(body.error.message).to.include('javascript:')
    })

    it('gives no <id> hint for an event handler without a tag', async () => {
      const { status, body } = await post('/api/v1/search', {
        note: 'click onclick=alert(1)',
      })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"note"')
      expect(body.error.message).not.to.include('<id>')
    })

    it('names an encoded script tag rather than tags or javascript: links', async () => {
      const { status, body } = await post('/api/v1/knowledgeBase', {
        name: '&lt;script&gt;alert(1)&lt;/script&gt;',
      })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"name"')
      expect(body.error.message).to.include('&lt;script')
      expect(body.error.message).not.to.include('<id>')
      expect(body.error.message).not.to.include('javascript:')
    })

    it('says an over-long value is too long rather than that it holds HTML', async () => {
      const { status, body } = await post('/api/v1/search', {
        text: 'a'.repeat(100001),
      })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"text"')
      expect(body.error.message).to.include('too long')
      expect(body.error.message).not.to.include('<id>')
    })

    it('does not repeat a field name that is itself markup', async () => {
      const { status, body } = await post('/api/v1/search', {
        '<img src=x onerror=alert(1)>': '<b>bold</b>',
      })

      expect(status).to.equal(400)
      expect(body.error.message).not.to.include('<img')
      expect(body.error.message).not.to.include('onerror')
    })
  })

  // -----------------------------------------------------------------------
  // Chat and search questions: free text, passed through unchanged
  // -----------------------------------------------------------------------
  describe('Chat and search questions', () => {
    let server: http.Server
    let baseUrl: string

    before(async () => {
      const app = express()
      // Same body limit as the app (src/app.ts), so long questions reach the middleware.
      app.use(express.json({ limit: '10mb' }))
      app.use(xssSanitizationMiddleware)
      // Echo the body as the route handler would receive it.
      app.all('*', (req, res) => {
        res.status(200).json({ received: req.body })
      })
      app.use(ErrorMiddleware.handleError())
      server = await new Promise<http.Server>((resolve) => {
        const s = app.listen(0, '127.0.0.1', () => resolve(s))
      })
      baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })

    after(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    })

    const post = async (path: string, body: unknown) => {
      const resp = await fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      return { status: resp.status, body: (await resp.json()) as any }
    }

    const toolCall = (name: string, args: Record<string, unknown>) => ({
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/call',
      params: { name, arguments: args },
    })

    const question = 'cite [Summary of <id>] and List<String>'

    it('accepts <id> in the question of an MCP pipeshub_chat call, unchanged', async () => {
      const { status, body } = await post('/mcp', toolCall('pipeshub_chat', { query: question }))

      expect(status).to.equal(200)
      expect(body.received.params.arguments.query).to.equal(question)
    })

    it('accepts <id> in the question of an MCP pipeshub_search call, unchanged', async () => {
      const { status, body } = await post('/mcp', toolCall('pipeshub_search', { query: question, limit: 5 }))

      expect(status).to.equal(200)
      expect(body.received.params.arguments.query).to.equal(question)
    })

    it('accepts <id> in the query of POST /api/v1/search, which MCP search calls, unchanged', async () => {
      const { status, body } = await post('/api/v1/search', { query: question, limit: 5 })

      expect(status).to.equal(200)
      expect(body.received.query).to.equal(question)
    })

    it('accepts <id> in the query of POST /api/v1/retrieve, unchanged', async () => {
      const { status, body } = await post('/api/v1/retrieve', { query: question, limit: 5 })

      expect(status).to.equal(200)
      expect(body.received.query).to.equal(question)
    })

    it('still refuses a script tag in the filters of POST /api/v1/retrieve', async () => {
      const { status } = await post('/api/v1/retrieve', {
        query: question,
        filters: { apps: ['<script>alert(1)</script>'] },
      })

      expect(status).to.equal(400)
    })

    it('still refuses a script tag in another argument of an MCP chat call', async () => {
      const { status, body } = await post(
        '/mcp',
        toolCall('pipeshub_chat', {
          query: question,
          filters: { apps: ['<script>alert(1)</script>'] },
        }),
      )

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"params.arguments.filters.apps[0]"')
    })

    it('still refuses a script tag in another field of POST /api/v1/search', async () => {
      const { status, body } = await post('/api/v1/search', {
        query: question,
        filters: { kb: ['<script>alert(1)</script>'] },
      })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"filters.kb[0]"')
    })

    it('still refuses a script tag elsewhere in the MCP request', async () => {
      const call = toolCall('pipeshub_chat', { query: question }) as any
      call.params._meta = { note: '<script>alert(1)</script>' }

      const { status, body } = await post('/mcp', call)

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"params._meta.note"')
    })

    it('still refuses a question over 100,000 characters as too long', async () => {
      const tooLong = 'a'.repeat(100001)
      const cases = [
        ['/mcp', toolCall('pipeshub_chat', { query: tooLong }), '"params.arguments.query"'],
        ['/mcp', toolCall('pipeshub_search', { query: tooLong }), '"params.arguments.query"'],
        ['/api/v1/search', { query: tooLong }, '"query"'],
      ] as const

      for (const [path, payload, field] of cases) {
        const { status, body } = await post(path, payload)

        expect(status).to.equal(400)
        expect(body.error.message).to.include(field)
        expect(body.error.message).to.include('longer than 100,000 characters')
      }
    })

    it('accepts a question of exactly 100,000 characters', async () => {
      const longest = 'a'.repeat(99995) + ' <id>'
      const { status, body } = await post('/mcp', toolCall('pipeshub_chat', { query: longest }))

      expect(status).to.equal(200)
      expect(body.received.params.arguments.query).to.equal(longest)
    })

    it('still checks the query of any other MCP tool', async () => {
      const { status, body } = await post(
        '/mcp',
        toolCall('pipeshub_get_record_content', { query: '<id>' }),
      )

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"params.arguments.query"')
    })

    it('still checks a query that is not a string', async () => {
      const { status } = await post(
        '/mcp',
        toolCall('pipeshub_chat', { query: { text: '<script>alert(1)</script>' } }),
      )

      expect(status).to.equal(400)
    })

    it('does not pass through a top-level key that only looks like the question path', async () => {
      const call = toolCall('pipeshub_chat', { query: question }) as any
      call['params.arguments.query'] = '<script>alert(1)</script>'

      const { status } = await post('/mcp', call)

      expect(status).to.equal(400)
    })

    it('still checks a field named query on other routes', async () => {
      const { status, body } = await post('/api/v1/knowledgeBase', { query: '<id>' })

      expect(status).to.equal(400)
      expect(body.error.message).to.include('"query"')
    })

    it('still checks a search query sent as a URL query parameter', async () => {
      const resp = await fetch(
        `${baseUrl}/api/v1/search?query=${encodeURIComponent('<id>')}`,
      )

      expect(resp.status).to.equal(400)
    })

    it('leaves the web chat route as it was: skipped entirely', async () => {
      const { status, body } = await post('/api/v1/conversations/stream', {
        query: question,
        note: '<b>kept</b>',
      })

      expect(status).to.equal(200)
      expect(body.received.query).to.equal(question)
      expect(body.received.note).to.equal('<b>kept</b>')
    })
  })
})
