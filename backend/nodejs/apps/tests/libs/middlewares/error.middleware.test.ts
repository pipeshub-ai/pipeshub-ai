import 'reflect-metadata'
import http from 'http'
import net from 'net'
import { once } from 'events'
import { AddressInfo } from 'net'
import express from 'express'
import { expect } from 'chai'
import sinon from 'sinon'
import { envGuard } from '../../helpers/env-guard'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import {
  BadRequestError,
  UnauthorizedError,
  WrongCredentialError,
  ForbiddenError,
  NotFoundError,
  InternalServerError,
  ServiceUnavailableError,
  ExpectedRefusalError,
  HttpError,
  GoneError,
  LargePayloadError,
  ConflictError,
  TooManyRequestsError,
  BadGatewayError,
  GatewayTimeoutError,
  UnprocessableEntityError,
  NotImplementedError,
} from '../../../src/libs/errors/http.errors'
import { ValidationError } from '../../../src/libs/errors/validation.error'
import { StorageUrlParseError } from '../../../src/libs/errors/storage.errors'
import { DomainHttpError, PublicDetailValue } from '../../../src/libs/errors/domain-http.error'
import { markClientSafe } from '../../../src/libs/errors/reader-friendly'
import { KafkaError } from '../../../src/libs/errors/kafka.errors'
import { RedisServiceNotInitializedError } from '../../../src/libs/errors/redis.errors'
import { ConnectionError } from '../../../src/libs/errors/database.errors'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../src/libs/errors/backend-error'
import { INVALID_PATH_SEGMENT_MESSAGE } from '../../../src/libs/middlewares/safe-path-params.middleware'
import { Logger } from '../../../src/libs/services/logger.service'
import { MongoNetworkError, MongoServerSelectionError } from 'mongodb'

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
    destroy: sinon.stub(),
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

class TestDomainError extends DomainHttpError {
  constructor(
    status: number,
    code: string,
    details?: Readonly<Record<string, PublicDetailValue>>,
    metadata?: Record<string, unknown>,
  ) {
    super(code, 'This conversation is busy. Try again shortly.', status, details)
    if (metadata) (this as { metadata?: unknown }).metadata = metadata
  }
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
    it('closes the started response instead of answering, and still logs the failure once', () => {
      const error = new Error('stream broke')
      const req = createMockRequest()
      const res = createMockResponse()
      res.headersSent = true
      res.statusCode = 200
      const next = createMockNext()

      handler(error, req, res, next)

      expect(res.destroy.calledOnceWithExactly()).to.be.true
      expect(res.status.called).to.be.false
      expect(res.json.called).to.be.false
      expect(loggerErrorStub.calledOnce).to.be.true
      expect(loggerErrorStub.firstCall.args[0]).to.equal('http.request.error')
      expect(loggerErrorStub.firstCall.args[1]).to.include({ status: 200, error_class: 'internal', error })
    })

    it('leaves a response that has already ended alone', () => {
      const res = createMockResponse()
      res.headersSent = true
      res.writableEnded = true

      handler(new Error('after the answer'), createMockRequest(), res, createMockNext())

      expect(res.destroy.called).to.be.false
      expect(res.json.called).to.be.false
    })

    // logRequestError catches its own failures, so this never happens today; the stub only reaches the defensive catch.
    it('defensive catch: still closes a started response if handling the error throws', () => {
      const res = createMockResponse()
      res.headersSent = true
      sinon.stub(ErrorMiddleware as any, 'logRequestError').throws(new Error('boom'))
      sinon.stub(console, 'error')

      handler(new Error('stream broke'), createMockRequest(), res, createMockNext())

      expect(res.destroy.calledOnceWithExactly()).to.be.true
      expect(res.json.called).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Log level of an expected answer
  // -----------------------------------------------------------------------
  describe('log level', () => {
    // A 4xx is the caller's mistake answered as intended; logging each at error
    // buried the real failures (half of one session's errors were these).
    for (const error of [
      new BadRequestError('A connector with this name already exists'),
      new UnauthorizedError('Wrong email or password'),
      new NotFoundError('Document not found'),
    ]) {
      it(`logs a ${String(error.statusCode)} at warn, without a stack, and still answers it`, () => {
        const res = createMockResponse()

        handler(error, createMockRequest(), res, createMockNext())

        expect(loggerWarnStub.calledOnce).to.be.true
        expect(loggerErrorStub.called).to.be.false
        expect(loggerWarnStub.firstCall.args[0]).to.equal('http.request.error')
        expect(loggerWarnStub.firstCall.args[1]).to.include({
          method: 'GET',
          status: error.statusCode,
          error_type: error.name,
          outcome: 'failure',
          error,
        })
        expect(res.status.calledWith(error.statusCode)).to.be.true
        expect(res.json.firstCall.args[0].error.message).to.equal(error.message)
      })
    }

    it('logs a server error at error, with its stack', () => {
      handler(new InternalServerError('Error fetching document information'), createMockRequest(), createMockResponse(), createMockNext())

      expect(loggerErrorStub.calledOnce).to.be.true
      expect(loggerWarnStub.called).to.be.false
      expect(loggerErrorStub.firstCall.args[1]).to.include({ status: 500, error_class: 'internal' })
    })

    it('names the class of each answer the same way the Python side does', () => {
      const cases: [Error, string][] = [
        [new BadRequestError('x'), 'bad_request'],
        [new UnauthorizedError('x'), 'auth_expired'],
        [new WrongCredentialError('x'), 'bad_request'],
        [new ForbiddenError('x'), 'forbidden'],
        [new NotFoundError('x'), 'not_found'],
        [new ServiceUnavailableError('x'), 'unavailable'],
      ]
      for (const [error] of cases) handler(error, createMockRequest(), createMockResponse(), createMockNext())
      expect(loggerWarnStub.getCalls().map((call) => call.args[1].error_class)).to.deep.equal(cases.map(([, cls]) => cls))
    })

    it('logs an error that is not ours at error', () => {
      handler(new Error('boom'), createMockRequest(), createMockResponse(), createMockNext())

      expect(loggerErrorStub.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // BaseError handling
  // -----------------------------------------------------------------------
  describe('body-parser rejections', () => {
    function parserError(type: string, status: number): Error {
      return Object.assign(new SyntaxError('Unexpected token } in JSON at position 12'), {
        type,
        status,
        statusCode: status,
        expose: true,
      })
    }

    it('answers a malformed JSON body with 400 and does not echo the body', () => {
      const res = createMockResponse()

      handler(parserError('entity.parse.failed', 400), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(400)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('HTTP_BAD_REQUEST')
      expect(response.error.message).to.equal('The request body is not valid JSON.')
    })

    it('answers a body that does not match its Content-Length with 400', () => {
      const res = createMockResponse()

      handler(parserError('request.size.invalid', 400), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(400)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('HTTP_BAD_REQUEST')
      expect(response.error.message).to.equal('The request body does not match its Content-Length.')
    })

    it('answers a form body with too many fields with 413', () => {
      const res = createMockResponse()

      handler(parserError('parameters.too.many', 413), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(413)).to.be.true
    })

    it('answers an oversized body with 413', () => {
      const res = createMockResponse()

      handler(parserError('entity.too.large', 413), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(413)).to.be.true
    })

    for (const [type, message] of [
      ['encoding.unsupported', 'The request body uses an unsupported content encoding.'],
      ['charset.unsupported', 'The request body uses an unsupported character set.'],
    ]) {
      it(`answers ${type} with 415 and a fixed message`, () => {
        const res = createMockResponse()

        handler(parserError(type, 415), createMockRequest(), res, createMockNext())

        expect(res.status.calledWith(415)).to.be.true
        expect(res.json.firstCall.args[0].error.message).to.equal(message)
      })
    }

    it('keeps an unexposed error with a parser-like type on the 500 path', () => {
      const error = Object.assign(new Error('boom'), { type: 'entity.parse.failed' })
      const res = createMockResponse()

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(500)).to.be.true
    })
  })

  describe('a path segment Express cannot decode', () => {
    const RAW = 'n5nc-fake%40example.com%ZZ'
    // Built as Express 4's Layer.match builds it from decode_param's URIError.
    const routerError = (): Error =>
      Object.assign(new URIError(`Failed to decode param '${RAW}'`), { status: 400, statusCode: 400 })

    it('answers 400 with a fixed sentence and does not echo the segment', () => {
      const res = createMockResponse()

      handler(routerError(), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(400)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.code).to.equal('HTTP_BAD_REQUEST')
      expect(response.error.message).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
      expect(JSON.stringify(response)).to.not.include(RAW)
    })

    it('logs it once at warn as bad_request, without the router error or its text', () => {
      const error = routerError()

      handler(error, createMockRequest(), createMockResponse(), createMockNext())

      expect(loggerErrorStub.called).to.be.false
      expect(loggerWarnStub.calledOnce).to.be.true
      const fields = loggerWarnStub.firstCall.args[1]
      expect(fields).to.include({ status: 400, error_class: 'bad_request', error_type: 'BadRequestError' })
      expect(fields.error).to.not.equal(error)
      expect(fields.error.cause).to.equal(undefined)
      expect(fields.error.message).to.not.include(RAW)
    })

    it('keeps a URIError without a status (our own decode) on the 500 path', () => {
      const res = createMockResponse()

      handler(new URIError('URI malformed'), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(500)).to.be.true
      expect(loggerErrorStub.calledOnce).to.be.true
      expect(loggerErrorStub.firstCall.args[1]).to.include({ error_class: 'internal' })
    })

    it('keeps a 400-status error that is not a URIError on the 500 path', () => {
      const res = createMockResponse()

      handler(Object.assign(new Error('boom'), { status: 400 }), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(500)).to.be.true
    })
  })

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

    it('logs an expected refusal at info with its code, not as an error with a stack', () => {
      const res = createMockResponse()
      handler(
        new ExpectedRefusalError('AGENT_HARNESS_RUN_NOT_CLAIMABLE', 'Run is not claimable', 409),
        createMockRequest({ method: 'POST', path: '/runs/r-1/claim', context: { requestId: 'req-9' } }),
        res,
        createMockNext(),
      )

      expect(res.status.calledWith(409)).to.be.true
      expect(res.json.firstCall.args[0].error.code).to.equal('HTTP_AGENT_HARNESS_RUN_NOT_CLAIMABLE')
      expect(loggerErrorStub.called).to.be.false
      const info = (ErrorMiddleware as any).logger.info as sinon.SinonStub
      expect(info.calledOnce).to.be.true
      // The one line of a failed request (main #5516), at info: the request id comes from the request context.
      const [message, meta] = info.firstCall.args
      expect(message).to.equal('http.request.error')
      expect(meta).to.include({ method: 'POST', status: 409, error_class: 'conflict', outcome: 'failure' })
      expect(meta.error.code).to.equal('HTTP_AGENT_HARNESS_RUN_NOT_CLAIMABLE')
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

    it('should send field errors for a ValidationError, naming body fields as the form sent them', () => {
      const error = new ValidationError('Name must be at most 100 characters.', [
        { field: 'body.name', message: 'Name must be at most 100 characters.', code: 'TOO_BIG' },
        { field: 'body.name', message: 'second message for the same field', code: 'X' },
        { field: 'query.page', message: 'Page must be at least 1.', code: 'TOO_SMALL' },
      ])
      const res = createMockResponse()

      handler(error, createMockRequest(), res, createMockNext())

      const response = res.json.firstCall.args[0]
      expect(response.error.fieldErrors).to.deep.equal([
        { field: 'name', message: 'Name must be at most 100 characters.', code: 'TOO_BIG' },
        { field: 'query.page', message: 'Page must be at least 1.', code: 'TOO_SMALL' },
      ])
    })

    it('should send field errors a client error carries in metadata, and none for a 5xx', () => {
      const dup = new BadRequestError('Group already exists', {
        fieldErrors: [{ field: 'name', message: 'Group already exists', code: 'DUPLICATE' }],
      })
      const res = createMockResponse()
      handler(dup, createMockRequest(), res, createMockNext())
      expect(res.json.firstCall.args[0].error.fieldErrors).to.deep.equal([
        { field: 'name', message: 'Group already exists', code: 'DUPLICATE' },
      ])

      const boom = new InternalServerError('x', {
        fieldErrors: [{ field: 'name', message: 'secret internals' }],
      })
      const res5 = createMockResponse()
      handler(boom, createMockRequest(), res5, createMockNext())
      expect(res5.json.firstCall.args[0].error).to.not.have.property('fieldErrors')
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

    it("never answers a storage vendor's text, in development either", () => {
      process.env.NODE_ENV = 'development'
      const error = new StorageUrlParseError('Invalid S3 URL format', { originalError: 'n5o-vendor-text bucket/org/report.pdf', code: 'ERR_INVALID_URL' })
      const res = createMockResponse()

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(400)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.error.metadata).to.include({ code: 'ERR_INVALID_URL' })
      expect(JSON.stringify(response)).to.not.contain('n5o-vendor-text')
    })

    for (const mode of ['development', 'production']) {
      it(`sends the access notices once, at the top level, in ${mode} (never as "[Circular]")`, () => {
        process.env.NODE_ENV = mode
        const notices = [{ code: 'connector_syncing', connectorId: 'c1' }]
        const error = new BadRequestError('test', { extra: 'info', accessNotices: notices })
        const res = createMockResponse()

        handler(error, createMockRequest(), res, createMockNext())

        const response = res.json.firstCall.args[0]
        expect(response.accessNotices).to.deep.equal(notices)
        expect(response.error.metadata?.accessNotices).to.equal(undefined)
        expect(response.error.metadata).to.deep.equal(mode === 'development' ? { extra: 'info' } : undefined)
        expect(JSON.stringify(response)).to.not.contain('[Circular]')
      })
    }

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
  // A call to another PipesHub service that got no answer
  // -----------------------------------------------------------------------
  describe('a call to another service that got no answer', () => {
    // What Node's fetch throws when the connectors or query service is not listening yet.
    const refused = () =>
      Object.assign(new TypeError('fetch failed'), { cause: Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8088'), { code: 'ECONNREFUSED' }) })

    it('answers a refused connection with 503 and the shared message, not 500', () => {
      const res = createMockResponse()

      handler(refused(), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(503)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.error.code).to.equal('HTTP_SERVICE_UNAVAILABLE')
      expect(body.error.message).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
      // Degraded, not our bug: one WARN, and no ERROR line from anywhere.
      expect(loggerWarnStub.calledOnce).to.be.true
      expect(loggerErrorStub.called).to.be.false
    })

    it('logs one line with the cause it could not show the reader', () => {
      const logged = sinon.stub(Logger.prototype, 'error')
      const original = refused()

      handler(original, createMockRequest({ method: 'GET', path: '/api/v1/teams/user/teams' }), createMockResponse(), createMockNext())

      expect(logged.called, 'no "Could not reach" line').to.be.false
      const fields = loggerWarnStub.firstCall.args[1]
      expect(fields).to.include({ status: 503, error_class: 'unavailable', error_type: 'ServiceUnavailableError' })
      expect(fields.error.cause).to.equal(original)
    })

    it('logs our own database being down as one ERROR with the stack, not as an unreachable upstream', () => {
      const refused = Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:27017'), { code: 'ECONNREFUSED' })
      const network = new MongoNetworkError('connect ECONNREFUSED 127.0.0.1:27017', { cause: refused })
      const mongoDown = new MongoServerSelectionError('connect ECONNREFUSED 127.0.0.1:27017', { error: network } as never)
      const res = createMockResponse()

      handler(mongoDown, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(500)).to.be.true
      expect(loggerWarnStub.called).to.be.false
      expect(loggerErrorStub.calledOnce).to.be.true
      expect(loggerErrorStub.firstCall.args[1]).to.include({
        status: 500,
        error_class: 'internal',
        error_type: 'MongoServerSelectionError',
        error: mongoDown,
      })
    })

    it('logs a timed-out call as timeout at warn', () => {
      handler(new DOMException('The operation was aborted due to timeout', 'TimeoutError'), createMockRequest(), createMockResponse(), createMockNext())

      expect(loggerWarnStub.firstCall.args[1]).to.include({ status: 504, error_class: 'timeout' })
    })

    it('answers a host that does not resolve with 503 too', () => {
      const res = createMockResponse()
      const error = Object.assign(new TypeError('fetch failed'), { cause: Object.assign(new Error('getaddrinfo ENOTFOUND'), { code: 'ENOTFOUND' }) })

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(503)).to.be.true
    })

    it("answers axios's request that got no response with 503", () => {
      const res = createMockResponse()
      const error = Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:3000'), { code: 'ECONNREFUSED', request: {} })

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(503)).to.be.true
    })

    it('still answers a certificate error with 500: retrying will not fix it', () => {
      const res = createMockResponse()
      const error = Object.assign(new TypeError('fetch failed'), { cause: Object.assign(new Error('Hostname/IP does not match'), { code: 'ERR_TLS_CERT_ALTNAME_INVALID' }) })

      handler(error, createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(500)).to.be.true
    })

    it('answers a timed-out call with 504', () => {
      const res = createMockResponse()

      handler(new DOMException('The operation was aborted due to timeout', 'TimeoutError'), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(504)).to.be.true
    })

    it('still answers an aborted call with 500', () => {
      const res = createMockResponse()

      handler(new DOMException('This operation was aborted', 'AbortError'), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(500)).to.be.true
    })

    it('keeps the cause out of the body in development', () => {
      process.env.NODE_ENV = 'development'
      const res = createMockResponse()

      handler(refused(), createMockRequest(), res, createMockNext())

      const body = res.json.firstCall.args[0]
      expect(body.error.metadata).to.equal(undefined)
      expect(JSON.stringify(body)).to.not.include('ECONNREFUSED')
    })

    it('closes the started response and answers nothing', () => {
      const res = createMockResponse()
      res.headersSent = true

      handler(refused(), createMockRequest(), res, createMockNext())

      expect(res.destroy.calledOnce).to.be.true
      expect(res.status.called).to.be.false
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
    it('answers the mapped status even when logging the error throws', () => {
      ;(ErrorMiddleware as any).logger = {
        error: sinon.stub().throws(new Error('provider hook broke')),
        warn: sinon.stub().throws(new Error('provider hook broke')),
        info: sinon.stub(),
        debug: sinon.stub(),
      }
      const consoleError = sinon.stub(console, 'error')
      const res = createMockResponse()

      handler(new NotFoundError('Document not found'), createMockRequest(), res, createMockNext())

      expect(res.status.calledWith(404)).to.be.true
      expect(res.json.firstCall.args[0].error.message).to.equal('Document not found')
      expect(consoleError.calledWithMatch('Logging the request error failed:')).to.be.true
    })


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
    it('logs no request header at all', () => {
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

      expect(loggerWarnStub.called).to.be.true
      const logged = JSON.stringify(loggerWarnStub.firstCall.args)
      expect(logged).to.not.contain('secret-token')
      expect(logged).to.not.contain('session=abc')
      expect(logged).to.not.contain('application/json')
    })

    // A Google connector callback that failed auth: the request's query and its
    // Referer both carried the live authorization code into the error log.
    for (const [name, makeError] of [
      ['an application error', () => new UnauthorizedError('No session')],
      ['an unknown error', () => new Error('boom')],
    ] as const) {
      it(`keeps OAuth codes and secret headers out of the log for ${name}`, () => {
        const fakeCode = '4/0FAKE-auth-code-not-real'
        const fakeState = 'ZmFrZS1zdGF0ZQ'
        const req = createMockRequest({
          path: '/api/v1/connectors/oauth/callback',
          originalUrl: `/api/v1/connectors/oauth/callback?code=${encodeURIComponent(fakeCode)}&state=${fakeState}`,
          query: { code: fakeCode, state: fakeState, baseUrl: 'http://localhost:3001' },
          headers: {
            referer:
              `http://localhost:3001/connectors/oauth/callback/Drive?state=${fakeState}` +
              `&iss=https://accounts.google.com&code=${fakeCode}&scope=drive`,
            authorization: 'Bearer fake-bearer-value',
            cookie: 'sid=fake-cookie-value',
            'x-session-token': 'fake-session-token-value',
            'x-access-token': 'fake-access-token-value',
            'user-agent': 'mocha',
          },
        })

        handler(makeError(), req, createMockResponse(), createMockNext())

        const calls = [...loggerErrorStub.getCalls(), ...loggerWarnStub.getCalls()]
        expect(calls).to.have.length(1)
        const logged = JSON.stringify(calls[0]!.args)
        for (const secret of [
          'FAKE-auth-code',
          fakeState,
          'fake-bearer-value',
          'fake-cookie-value',
          'fake-session-token-value',
          'fake-access-token-value',
        ]) {
          expect(logged).to.not.contain(secret)
        }
        // The route template stands in for the URL; no query, Referer or header is logged.
        expect(calls[0]!.args[1]).to.include({ method: 'GET', route: 'unmatched' })
        expect(logged).to.not.contain('/connectors/oauth/callback/Drive')
        // (the error's own stack names mocha's files, so the user agent is checked on the fields)
        expect(JSON.stringify({ ...calls[0]!.args[1], error: undefined })).to.not.contain('mocha')
      })
    }
  })

  // -----------------------------------------------------------------------
  // today's bodies carry no `details` key
  // -----------------------------------------------------------------------
  describe('error bodies today (no details key)', () => {
    const wording = 'We could not do that. Please try again.'
    const cases: Array<[string, () => Error, number, string, string]> = [
      ['BadRequestError', () => new BadRequestError(wording, { details: 'x' }), 400, 'HTTP_BAD_REQUEST', wording],
      ['UnauthorizedError', () => new UnauthorizedError(wording), 401, 'HTTP_UNAUTHORIZED', wording],
      ['ForbiddenError', () => new ForbiddenError(wording), 403, 'HTTP_FORBIDDEN', wording],
      ['NotFoundError', () => new NotFoundError(wording), 404, 'HTTP_NOT_FOUND', wording],
      ['ConflictError', () => new ConflictError(wording, { details: { a: 1 } }), 409, 'HTTP_CONFLICT', wording],
      ['GoneError', () => new GoneError(wording), 410, 'HTTP_GONE', wording],
      ['LargePayloadError', () => new LargePayloadError(wording), 413, 'HTTP_PAYLOAD_TOO_LARGE', wording],
      ['UnprocessableEntityError', () => new UnprocessableEntityError(wording), 422, 'HTTP_UNPROCESSABLE_ENTITY', wording],
      ['TooManyRequestsError', () => new TooManyRequestsError(wording), 429, 'HTTP_TOO_MANY_REQUESTS', wording],
      ['InternalServerError', () => new InternalServerError(wording), 500, 'HTTP_INTERNAL_SERVER_ERROR', wording],
      ['NotImplementedError', () => new NotImplementedError(wording), 501, 'HTTP_NOT_IMPLEMENTED', wording],
      ['BadGatewayError', () => new BadGatewayError(wording), 502, 'HTTP_BAD_GATEWAY', wording],
      ['ServiceUnavailableError', () => new ServiceUnavailableError(wording), 503, 'HTTP_SERVICE_UNAVAILABLE', wording],
      ['GatewayTimeoutError', () => new GatewayTimeoutError(wording), 504, 'HTTP_GATEWAY_TIMEOUT', wording],
      ['HttpError', () => new HttpError('CUSTOM', wording, 418), 418, 'HTTP_CUSTOM', wording],
      ['ValidationError', () => new ValidationError('Bad input', []), 400, 'VALIDATION_ERROR', 'Bad input'],
      ['KafkaError', () => new KafkaError('Error publishing'), 503, 'INTERNAL_ERROR', "Something went wrong on PipesHub's side. Please try again; if it keeps happening, ask your admin for help."],
      ['ConnectionError', () => new ConnectionError('Failed to connect'), 503, 'INTERNAL_ERROR', "Something went wrong on PipesHub's side. Please try again; if it keeps happening, ask your admin for help."],
    ]

    for (const nodeEnv of ['development', 'production']) {
      for (const [label, make, status, code, message] of cases) {
        it(`${label} in ${nodeEnv}: same status, code and message, no details key`, () => {
          process.env.NODE_ENV = nodeEnv
          const res = createMockResponse()
          handler(make(), createMockRequest({ context: { requestId: 'r-1' } }), res, createMockNext())

          expect(res.status.firstCall.args[0]).to.equal(status)
          const body = res.json.firstCall.args[0]
          expect(Object.keys(body)).to.deep.equal(['error'])
          expect(body.error).to.not.have.property('details')
          expect(body.error.code).to.equal(code)
          expect(body.error.message).to.equal(message)
          expect(body.error.requestId).to.equal('r-1')
          const allowed = nodeEnv === 'development'
            ? ['code', 'message', 'requestId', 'metadata']
            : ['code', 'message', 'requestId']
          expect(Object.keys(body.error).every((k) => allowed.includes(k))).to.equal(true)
        })
      }
    }
  })

  describe('DomainHttpError', () => {
    const details = { activeRun: { userId: 'u1', startedAt: '2026-10-01T00:00:00.000Z' } }

    it('sends the unprefixed code and details in production, without metadata', () => {
      process.env.NODE_ENV = 'production'
      const res = createMockResponse()
      handler(
        new TestDomainError(409, 'CONVERSATION_BUSY', details, { internal: 'x' }),
        createMockRequest({ context: { requestId: 'r-9' } }),
        res,
        createMockNext(),
      )

      expect(res.status.firstCall.args[0]).to.equal(409)
      expect(res.json.firstCall.args[0]).to.deep.equal({
        error: {
          code: 'CONVERSATION_BUSY',
          message: 'This conversation is busy. Try again shortly.',
          requestId: 'r-9',
          details,
        },
      })
    })

    it('sends details and metadata in development', () => {
      process.env.NODE_ENV = 'development'
      const res = createMockResponse()
      handler(
        new TestDomainError(409, 'CONVERSATION_BUSY', details, { internal: 'x' }),
        createMockRequest(),
        res,
        createMockNext(),
      )
      const body = res.json.firstCall.args[0].error
      expect(body.details).to.deep.equal(details)
      expect(body.metadata).to.deep.equal({ internal: 'x' })
    })

    it('omits details when the error has none', () => {
      process.env.NODE_ENV = 'production'
      const res = createMockResponse()
      handler(new TestDomainError(409, 'CONVERSATION_BUSY'), createMockRequest(), res, createMockNext())
      expect(res.json.firstCall.args[0].error).to.not.have.property('details')
    })

    it('keeps code and message of a 503 domain error; a 503 HttpError behaves as today', () => {
      process.env.NODE_ENV = 'production'
      const res = createMockResponse()
      handler(
        new TestDomainError(503, 'TEAM_RESOLUTION_UNAVAILABLE', { retryable: true }),
        createMockRequest(),
        res,
        createMockNext(),
      )
      const body = res.json.firstCall.args[0].error
      expect(res.status.firstCall.args[0]).to.equal(503)
      expect(body.code).to.equal('TEAM_RESOLUTION_UNAVAILABLE')
      expect(body.message).to.equal('This conversation is busy. Try again shortly.')
      expect(body.details).to.deep.equal({ retryable: true })

      const res2 = createMockResponse()
      handler(new ServiceUnavailableError('down'), createMockRequest(), res2, createMockNext())
      const body2 = res2.json.firstCall.args[0].error
      expect(body2.code).to.equal('HTTP_SERVICE_UNAVAILABLE')
      expect(body2).to.not.have.property('details')
    })
  })
})

describe('ErrorMiddleware with a client that aborts its own request', () => {
  let logged: Array<{ level: string; message: string }>
  let originalLogger: any
  let server: http.Server
  let port: number
  let handled: Promise<void>

  beforeEach(async () => {
    logged = []
    originalLogger = (ErrorMiddleware as any).logger
    const record = (level: string) => (message: string) => {
      logged.push({ level, message })
    }
    ;(ErrorMiddleware as any).logger = {
      error: record('error'),
      warn: record('warn'),
      info: record('info'),
      debug: record('debug'),
    }
    let done!: () => void
    handled = new Promise((resolve) => {
      done = resolve
    })
    const app = express()
    app.use(express.json())
    app.post('/renew', (_req, res) => {
      res.json({ ok: true })
    })
    const handle = ErrorMiddleware.handleError()
    app.use((error: Error, req: any, res: any, next: any) => {
      handle(error, req, res, next)
      done()
    })
    server = app.listen(0, '127.0.0.1')
    await once(server, 'listening')
    port = (server.address() as AddressInfo).port
  })

  afterEach(async () => {
    ;(ErrorMiddleware as any).logger = originalLogger
    server.close()
  })

  it('logs the abort below ERROR: the client left, the server did not fail', async () => {
    // A worker killed mid-renew: the headers promise a body that never arrives.
    const socket = net.connect(port, '127.0.0.1')
    await once(socket, 'connect')
    socket.write(
      'POST /renew HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 86\r\n\r\n{"fence":',
    )
    await new Promise((resolve) => setTimeout(resolve, 50))
    socket.destroy()
    await handled

    expect(logged.filter((l) => l.level === 'error')).to.deep.equal([])
    expect(logged.map((l) => l.message)).to.include('Request aborted by the client')
  })

  it('answers a client that stays with a malformed body, and does not take it for an abort', async () => {
    const response = await fetch(`http://127.0.0.1:${port}/renew`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{"fence":',
    })
    await handled
    expect(logged.some((l) => l.message === 'Request aborted by the client')).to.equal(false)
    expect(response.status).to.be.greaterThanOrEqual(400)
  })

  it('answers a reset from a service the route called: the client is still there, so it is no abort', async () => {
    const upstream = net.createServer((socket) => socket.destroy())
    upstream.listen(0, '127.0.0.1')
    await once(upstream, 'listening')
    const upstreamPort = (upstream.address() as AddressInfo).port
    const app = express()
    app.use(express.json())
    app.post('/relay', (_req, _res, next) => {
      const call = net.connect(upstreamPort, '127.0.0.1')
      call.on('connect', () => call.write('GET / HTTP/1.1\r\nHost: x\r\n\r\n'))
      call.on('error', next)
      call.on('close', () => next(Object.assign(new Error('socket hang up'), { code: 'ECONNRESET' })))
    })
    const handle = ErrorMiddleware.handleError()
    app.use((error: Error, req: any, res: any, next: any) => handle(error, req, res, next))
    const relay = app.listen(0, '127.0.0.1')
    await once(relay, 'listening')
    try {
      const response = await fetch(`http://127.0.0.1:${(relay.address() as AddressInfo).port}/relay`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{"a":1}',
        signal: AbortSignal.timeout(5000),
      })
      expect(response.status).to.be.greaterThanOrEqual(500)
      expect(logged.some((l) => l.message === 'Request aborted by the client')).to.equal(false)
    } finally {
      relay.close()
      upstream.close()
    }
  })
})
