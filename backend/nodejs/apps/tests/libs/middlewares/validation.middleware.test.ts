import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z } from 'zod'
import {
  UNREADABLE_REQUEST_MESSAGE,
  ValidationMiddleware,
} from '../../../src/libs/middlewares/validation.middleware'
import { ValidationError } from '../../../src/libs/errors/validation.error'
import { ServiceUnavailableError } from '../../../src/libs/errors/http.errors'
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
        query: z.object({}).loose(),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
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
        body: z.object({}).loose(),
        query: z.object({
          page: z.string(),
          limit: z.string(),
        }),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
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
        body: z.object({}).loose(),
        query: z.object({}).loose(),
        params: z.object({
          id: z.string().uuid(),
        }),
        headers: z.object({}).loose(),
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
        query: z.object({}).loose(),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { name: 'Alice' }, // missing email
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ValidationError)
      expect(error.message).to.equal(error.errors[0].message)
      expect(error.errors).to.be.an('array')
      expect(error.errors.length).to.be.greaterThan(0)
    })

    it('should call next with ValidationError when body has wrong type', async () => {
      const schema = z.object({
        body: z.object({
          count: z.number(),
        }),
        query: z.object({}).loose(),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { count: 'not-a-number' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ValidationError)
    })

    it('should call next with ValidationError for invalid email format', async () => {
      const schema = z.object({
        body: z.object({
          email: z.string().email(),
        }),
        query: z.object({}).loose(),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: { email: 'not-an-email' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ValidationError)
    })

    it('should call next with ValidationError when params are invalid', async () => {
      const schema = z.object({
        body: z.object({}).loose(),
        query: z.object({}).loose(),
        params: z.object({
          id: z.string().uuid(),
        }),
        headers: z.object({}).loose(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        params: { id: 'not-a-uuid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ValidationError)
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
        query: z.object({}).loose(),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
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
        }).loose(),
        query: z.object({}).loose(),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
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
    const schemaWithBody = (body: z.ZodTypeAny) =>
      z.object({ body, query: z.any(), params: z.any(), headers: z.any() })

    const run = async (schema: z.ZodObject, req: any = createMockRequest()) => {
      const next = createMockNext()
      await ValidationMiddleware.validate(schema)(req, createMockResponse(), next)
      expect(next.calledOnce).to.be.true
      return next.firstCall.args[0]
    }

    const GENERIC_REFUSAL = [
      { field: '', message: UNREADABLE_REQUEST_MESSAGE, code: 'VALIDATION_ERROR', value: '' },
    ]

    it('answers a plain Error thrown by a transform with the fixed 400, without its message', async () => {
      const error = await run(
        schemaWithBody(
          z.any().transform(() => {
            throw new Error('n5m-canary sent-value')
          }),
        ),
      )

      expect(error).to.be.instanceOf(ValidationError)
      expect(error.statusCode).to.equal(400)
      expect(error.errors).to.deep.equal(GENERIC_REFUSAL)
      expect(JSON.stringify(error)).to.not.include('n5m-canary')
      expect(error.message).to.not.include('n5m-canary')
    })

    it('answers a TypeError from a refine with the fixed 400', async () => {
      const error = await run(
        schemaWithBody(z.any().refine(() => (null as any).principalId)),
      )

      expect(error).to.be.instanceOf(ValidationError)
      expect(error.errors).to.deep.equal(GENERIC_REFUSAL)
    })

    it('still answers a call-stack RangeError with the too-many-values refusal', async () => {
      const error = await run(
        schemaWithBody(
          z.any().refine(() => {
            throw new RangeError('Maximum call stack size exceeded')
          }),
        ),
      )

      expect(error).to.be.instanceOf(ValidationError)
      expect(error.errors).to.deep.equal([
        {
          field: '',
          message: 'The request holds too many invalid values to check.',
          code: 'TOO_BIG',
          value: '',
        },
      ])
    })

    it('answers any other RangeError with the fixed 400, not the too-many-values refusal', async () => {
      const error = await run(
        schemaWithBody(
          z.any().refine(() => {
            throw new RangeError('Invalid time value')
          }),
        ),
      )

      expect(error).to.be.instanceOf(ValidationError)
      expect(error.errors).to.deep.equal(GENERIC_REFUSAL)
    })

    it('passes our own classified error through with its status', async () => {
      const unavailable = new ServiceUnavailableError('store is down')
      const error = await run(
        schemaWithBody(
          z.any().refine(async () => {
            throw unavailable
          }),
        ),
      )

      expect(error).to.equal(unavailable)
      expect(error.statusCode).to.equal(503)
    })

    it('forwards a throw outside the parse as is, not as a 400', async () => {
      // Express 5 makes req.query getter-only; assigning it then throws in our code, not the caller's.
      const req = createMockRequest({ body: {} })
      Object.defineProperty(req, 'query', { get: () => ({}), enumerable: true })

      const error = await run(schemaWithBody(z.object({})), req)

      expect(error).to.be.instanceOf(TypeError)
      expect(error).to.not.be.instanceOf(ValidationError)
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
        headers: z.object({}).loose(),
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
        query: z.object({}).loose(),
        params: z.object({}).loose(),
        headers: z.object({}).loose(),
      })

      const middleware = ValidationMiddleware.validate(schema)
      const req = createMockRequest({
        body: {}, // missing all fields
      })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ValidationError)
      expect(error.errors.length).to.be.greaterThanOrEqual(3)
    })
  })
})
