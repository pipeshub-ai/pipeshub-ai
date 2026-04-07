import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z } from 'zod'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
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
    method: 'POST',
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

describe('ValidationMiddleware', () => {
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
  // Happy path
  // -----------------------------------------------------------------------
  describe('validate - happy path', () => {
    it('should call next() when body matches schema', async () => {
      const schema = z.object({
        body: z.object({
          name: z.string(),
          age: z.number(),
        }),
        query: z.object({}).passthrough(),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { name: 'Alice', age: 30 },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.name).to.equal('Alice')
      expect(req.body.age).to.equal(30)
    })

    it('should call next() when query params match schema', async () => {
      const schema = z.object({
        body: z.object({}).passthrough(),
        query: z.object({
          page: z.string(),
          limit: z.string(),
        }),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        query: { page: '1', limit: '10' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next() when params match schema', async () => {
      const schema = z.object({
        body: z.object({}).passthrough(),
        query: z.object({}).passthrough(),
        params: z.object({
          id: z.string().uuid(),
        }),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        params: { id: '550e8400-e29b-41d4-a716-446655440000' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // Validation failures
  // -----------------------------------------------------------------------
  describe('validate - validation failures', () => {
    it('should call next with ValidationError when body is missing required field', async () => {
      const schema = z.object({
        body: z.object({
          name: z.string(),
          email: z.string().email(),
        }),
        query: z.object({}).passthrough(),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { name: 'Alice' }, // missing email
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should log and continue when body has wrong type', async () => {
      const schema = z.object({
        body: z.object({
          count: z.number(),
        }),
        query: z.object({}).passthrough(),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { count: 'not-a-number' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should log and continue for invalid email format', async () => {
      const schema = z.object({
        body: z.object({
          email: z.string().email(),
        }),
        query: z.object({}).passthrough(),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { email: 'not-an-email' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should log and continue when params are invalid', async () => {
      const schema = z.object({
        body: z.object({}).passthrough(),
        query: z.object({}).passthrough(),
        params: z.object({
          id: z.string().uuid(),
        }),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        params: { id: 'not-a-uuid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // stripUnknown option
  // -----------------------------------------------------------------------
  describe('validate - stripUnknown option', () => {
    it('should strip unknown fields when stripUnknown is true', async () => {
      const schema = z.object({
        body: z.object({
          name: z.string(),
        }),
        query: z.object({}).passthrough(),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema, { stripUnknown: true })
      const req = createMockRequest({
        body: { name: 'Alice', extraField: 'should-be-removed' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.name).to.equal('Alice')
      expect(req.body.extraField).to.be.undefined
    })

    it('should pass through unknown fields when stripUnknown is false (default)', async () => {
      const schema = z.object({
        body: z.object({
          name: z.string(),
        }).passthrough(),
        query: z.object({}).passthrough(),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { name: 'Alice', extraField: 'should-remain' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.extraField).to.equal('should-remain')
    })
  })

  // -----------------------------------------------------------------------
  // Non-Zod errors
  // -----------------------------------------------------------------------
  describe('validate - non-Zod errors', () => {
    it('should log and continue on non-Zod errors', async () => {
      // Create a schema that deliberately throws a non-Zod error
      const schema = z.object({
        body: z.any(),
        query: z.any(),
        params: z.any(),
        headers: z.any(),
      })

      // Override parseAsync to throw a generic error
      sinon.stub(schema, 'passthrough').returns({
        parseAsync: sinon.stub().rejects(new Error('Unexpected error')),
      } as any)

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // Replaces request properties
  // -----------------------------------------------------------------------
  describe('validate - request property replacement', () => {
    it('should replace req.body, req.query, and req.params with validated data', async () => {
      const schema = z.object({
        body: z.object({
          name: z.string().trim(),
        }),
        query: z.object({
          search: z.string().trim(),
        }),
        params: z.object({
          id: z.string(),
        }),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { name: '  Alice  ' },
        query: { search: '  hello  ' },
        params: { id: '123' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.name).to.equal('Alice')
      expect(req.query.search).to.equal('hello')
      expect(req.params.id).to.equal('123')
    })
  })

  // -----------------------------------------------------------------------
  // Multiple validation errors
  // -----------------------------------------------------------------------
  describe('validate - multiple errors', () => {
    it('should report multiple validation errors at once', async () => {
      const schema = z.object({
        body: z.object({
          name: z.string(),
          age: z.number(),
          email: z.string().email(),
        }),
        query: z.object({}).passthrough(),
        params: z.object({}).passthrough(),
        headers: z.object({}).passthrough(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: {}, // missing all fields
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })
})
