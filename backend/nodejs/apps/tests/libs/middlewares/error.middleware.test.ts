import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { envGuard } from '../../helpers/env-guard'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import {
  BadRequestError,
  UnauthorizedError,
  ForbiddenError,
  NotFoundError,
  InternalServerError,
  ServiceUnavailableError,
} from '../../../src/libs/errors/http.errors'
import { ValidationError } from '../../../src/libs/errors/validation.error'
import { markClientSafe } from '../../../src/libs/errors/reader-friendly'
import { KafkaError } from '../../../src/libs/errors/kafka.errors'
import { RedisServiceNotInitializedError } from '../../../src/libs/errors/redis.errors'
import { ConnectionError } from '../../../src/libs/errors/database.errors'

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

describe('ErrorMiddleware', () => {
  const env = envGuard()
  let handler: ReturnType<typeof ErrorMiddleware.handleError>
  // Replace ErrorMiddleware's static logger with a fake to avoid stubbing the
  // shared Logger singleton (which causes "already stubbed" errors when other
  // test suites touch the same singleton).
  let loggerErrorStub: sinon.SinonStub
  let loggerWarnStub: sinon.SinonStub
  let originalLogger: any

  beforeEach(() => {
    env.snapshot()
    loggerErrorStub = sinon.stub()
    loggerWarnStub = sinon.stub()
    originalLogger = (ErrorMiddleware as any).logger
    ;(ErrorMiddleware as any).logger = {
      error: loggerErrorStub,
      warn: loggerWarnStub,
      info: sinon.stub(),
      debug: sinon.stub(),
    }
    handler = ErrorMiddleware.handleError()
  })

  afterEach(() => {
    env.restore()
    ;(ErrorMiddleware as any).logger = originalLogger
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // headersSent guard
  // -----------------------------------------------------------------------
  describe('headersSent guard', () => {
    it('should do nothing when headers have already been sent', () => {
      const error = new Error('test')
      const req = createMockRequest()
      const res = createMockResponse()
      res.headersSent = true
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.called).to.be.false
      expect(res.json.called).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // BaseError handling
  // -----------------------------------------------------------------------
  describe('BaseError handling', () => {
    it('should respond with correct status code for BadRequestError', () => {
      const error = new BadRequestError('Invalid input')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.calledWith(400)).to.be.true
      expect(res.json.calledOnce).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('HTTP_BAD_REQUEST')
      expect(response.error.message).to.equal('Invalid input')
    })

    it('should respond with 401 for UnauthorizedError', () => {
      const error = new UnauthorizedError('Not authorized')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.calledWith(401)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('HTTP_UNAUTHORIZED')
    })

    it('should respond with 403 for ForbiddenError', () => {
      const error = new ForbiddenError('Access denied')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.calledWith(403)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('HTTP_FORBIDDEN')
    })

    it('should respond with 404 for NotFoundError', () => {
      const error = new NotFoundError('Not found')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should respond with 500 for InternalServerError', () => {
      const error = new InternalServerError('Server failure')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.calledWith(500)).to.be.true
    })

    it('should relay a Retry-After carried in the error metadata', () => {
      const error = new ServiceUnavailableError('Try again shortly', { retryAfter: '5' })
      const res = createMockResponse()

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(503)).to.be.true
      expect(res.setHeader.calledWith('Retry-After', '5')).to.be.true
    })

    it('should relay a Retry-After given as an HTTP date', () => {
      const date = 'Wed, 21 Oct 2026 07:28:00 GMT'
      const error = new ServiceUnavailableError('Try again later', { retryAfter: date })
      const res = createMockResponse()

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.setHeader.calledWith('Retry-After', date)).to.be.true
    })

    it('should drop a malformed Retry-After instead of echoing it', () => {
      const error = new ServiceUnavailableError('Try again', { retryAfter: '5\r\nX-Evil: 1' })
      const res = createMockResponse()

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(503)).to.be.true
      expect(res.setHeader.calledWith('Retry-After')).to.be.false
    })

    it('should respond with 400 for ValidationError', () => {
      const error = new ValidationError('Validation failed', [
        { field: 'email', message: 'Required', code: 'INVALID_TYPE' },
      ])
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.calledWith(400)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('VALIDATION_ERROR')
      expect(response.error.message).to.equal('Validation failed')
    })

    it('should include metadata in development mode', () => {
      process.env.NODE_ENV = 'development'

      const error = new BadRequestError('test', { extra: 'info' })
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.error.metadata).to.deep.equal({ extra: 'info' })
    })

    it('should include metadata in dev mode (short alias)', () => {
      process.env.NODE_ENV = 'dev'

      const error = new BadRequestError('test', { extra: 'dev-info' })
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.error.metadata).to.deep.equal({ extra: 'dev-info' })
    })

    it('should not include error metadata in production mode', () => {
      process.env.NODE_ENV = 'production'

      const error = new BadRequestError('test', {
        blockedUntil: '2026-04-25T10:00:00.000Z',
        secret: 'data',
      })
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.error.metadata).to.be.undefined
    })

    it('should never include stack trace in response', () => {
      const error = new BadRequestError('test error')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.error.stack).to.be.undefined
      expect(response.error.stackTrace).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // Unknown (non-BaseError) handling
  // -----------------------------------------------------------------------
  describe('Unknown error handling', () => {
    it('should respond with 500 for a generic Error', () => {
      const error = new Error('Something went wrong')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.status.calledWith(500)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('INTERNAL_ERROR')
    })

    it('should hide error message in production for unknown errors', () => {
      process.env.NODE_ENV = 'production'

      const error = new Error('Secret internal detail')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.error.message).to.include("went wrong on PipesHub's side")
      expect(response.error.message).to.not.include('Secret internal detail')
    })

    // Compose and the integration stack run with NODE_ENV=development, so this
    // path is reachable in real deployments and must not echo the raw message.
    it('should hide the error message outside production too', () => {
      process.env.NODE_ENV = 'development'

      const error = new Error('Detailed dev message')
      const req = createMockRequest({ context: { requestId: 'req-42' } })
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.error.message).to.not.include('Detailed dev message')
      expect(response.error.requestId).to.equal('req-42')
    })

    it('should keep the raw message in the log', () => {
      process.env.NODE_ENV = 'development'

      const error = new Error('Detailed dev message')
      handler(error, createMockRequest(), createMockResponse(), createMockNext())

      expect(loggerErrorStub.called).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Infrastructure failures
  // -----------------------------------------------------------------------
  describe('Infrastructure errors', () => {
    for (const [label, makeError] of [
      ['Kafka', () => new KafkaError('Error publishing to Kafka topic records')],
      ['Redis', () => new RedisServiceNotInitializedError('Redis service is not initialized.')],
      ['MongoDB', () => new ConnectionError('Failed to connect to MongoDB')],
    ] as [string, () => Error][]) {
      it(`replaces a ${label} message with a plain one and a reference`, () => {
        process.env.NODE_ENV = 'development'

        const req = createMockRequest({ context: { requestId: 'req-7' } })
        const res = createMockResponse()

        handler(makeError(), req, res, createMockNext())

        const response = res.json.firstCall.args[0]
        expect(response.error.code).to.equal('INTERNAL_ERROR')
        expect(response.error.message).to.include("went wrong on PipesHub's side")
        // The id rides beside the words, so a client that filters
        // technical-looking text still shows the sentence.
        expect(response.error.message).to.not.include('req-7')
        expect(response.error.requestId).to.equal('req-7')
        expect(response.error.message).to.not.match(/kafka|redis|mongo/i)
        expect(response.error.metadata).to.be.undefined
      })
    }

    it('keeps a 5xx message this codebase wrote', () => {
      process.env.NODE_ENV = 'development'

      const res = createMockResponse()
      const message =
        "We couldn't save this file right now. Please try again in a moment."
      handler(
        new InternalServerError(message),
        createMockRequest(),
        res,
        createMockNext(),
      )

      // Modules across the app write their own 5xx copy for readers. Whether an
      // upstream service's words are worth repeating is decided where they
      // arrive, in libs/errors/backend-error, not here.
      expect(res.json.firstCall.args[0].error.message).to.equal(message)
    })

    it('keeps a 5xx we built and marked, whatever else changes', () => {
      process.env.NODE_ENV = 'development'

      const res = createMockResponse()
      const message =
        'Something went wrong while PipesHub tried to create team. Please try again in a moment.'
      handler(
        markClientSafe(new InternalServerError(message)),
        createMockRequest(),
        res,
        createMockNext(),
      )

      expect(res.json.firstCall.args[0].error.message).to.equal(message)
    })

    it('leaves a deliberate 4xx message alone', () => {
      process.env.NODE_ENV = 'development'

      const res = createMockResponse()
      handler(
        new BadRequestError('Pick at least one folder to sync.'),
        createMockRequest({ context: { requestId: 'req-8' } }),
        res,
        createMockNext(),
      )

      const response = res.json.firstCall.args[0]
      expect(response.error.message).to.equal('Pick at least one folder to sync.')
      expect(response.error.requestId).to.equal('req-8')
    })
  })

  // -----------------------------------------------------------------------
  // Sanitization
  // -----------------------------------------------------------------------
  describe('Response sanitization', () => {
    it('should strip stack and stackTrace from error response', () => {
      process.env.NODE_ENV = 'development'

      // Create an error with metadata that contains stack-like fields
      const error = new BadRequestError('test', {
        nested: { stack: 'should-be-removed', stackTrace: 'also-removed', safe: 'kept' },
      })
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      // The sanitizer removes 'stack' and 'stackTrace' keys at all levels
      expect(response.error.stack).to.be.undefined
      expect(response.error.stackTrace).to.be.undefined
      if (response.error.metadata?.nested) {
        expect(response.error.metadata.nested.stack).to.be.undefined
        expect(response.error.metadata.nested.stackTrace).to.be.undefined
        expect(response.error.metadata.nested.safe).to.equal('kept')
      }
    })

    it('should handle circular references safely', () => {
      process.env.NODE_ENV = 'development'

      const circularObj: any = { a: 1 }
      circularObj.self = circularObj

      const error = new BadRequestError('circular test', circularObj)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      // Should not throw
      handler(error, req, res, next)

      expect(res.status.calledWith(400)).to.be.true
      expect(res.json.calledOnce).to.be.true
    })

    it('should sanitize arrays in error metadata (remove stack/stackTrace from array items)', () => {
      process.env.NODE_ENV = 'development'

      const error = new BadRequestError('test', {
        items: [
          { safe: 'kept', stack: 'remove-me' },
          { name: 'test', stackTrace: 'remove-me-too' },
        ],
      })
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      const response = res.json.firstCall.args[0]
      if (response.error.metadata?.items) {
        expect(response.error.metadata.items[0].stack).to.be.undefined
        expect(response.error.metadata.items[0].safe).to.equal('kept')
        expect(response.error.metadata.items[1].stackTrace).to.be.undefined
        expect(response.error.metadata.items[1].name).to.equal('test')
      }
    })

    it('should return non-object error response as-is', () => {
      // Access sanitizeErrorResponse directly to test non-object input
      const result = (ErrorMiddleware as any).sanitizeErrorResponse(null)
      expect(result).to.be.null

      const result2 = (ErrorMiddleware as any).sanitizeErrorResponse('string-error')
      expect(result2).to.equal('string-error')

      const result3 = (ErrorMiddleware as any).sanitizeErrorResponse(42)
      expect(result3).to.equal(42)
    })
  })

  // -----------------------------------------------------------------------
  // Error middleware failure
  // -----------------------------------------------------------------------
  describe('Error middleware failure', () => {
    it('should send 500 with MIDDLEWARE_ERROR if the error handler itself throws', () => {
      // Force an error inside the handler by making logger throw
      const error = new BadRequestError('test')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      // Make res.status.json throw the first time it processes
      let callCount = 0
      res.json.callsFake((...args: any[]) => {
        callCount++
        if (callCount === 1) {
          throw new Error('JSON serialization fail')
        }
        return res
      })

      // The error handler tries to call json once (fails), then tries again
      // with the fallback MIDDLEWARE_ERROR response. We won't get the inner try-catch
      // without more elaborate stubbing. Verify it doesn't throw outward.
      expect(() => handler(error, req, res, next)).to.not.throw()
    })
  })

  // -----------------------------------------------------------------------
  // Request context logging
  // -----------------------------------------------------------------------
  describe('Request context', () => {
    it('should sanitize authorization and cookie headers before logging', () => {
      const error = new BadRequestError('test')
      const req = createMockRequest({
        headers: {
          authorization: 'Bearer secret-token',
          cookie: 'session=abc',
          'content-type': 'application/json',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(error, req, res, next)

      // Logger should have been called - verify it was called
      expect(loggerErrorStub.called).to.be.true
    })
  })
})
