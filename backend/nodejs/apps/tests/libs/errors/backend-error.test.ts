import { expect } from 'chai'
import sinon from 'sinon'
import {
  handleBackendError,
  KB_BEING_DELETED_MESSAGE,
  SERVICE_UNAVAILABLE_MESSAGE,
  unansweredCallError,
} from '../../../src/libs/errors/backend-error'
import { Logger } from '../../../src/libs/services/logger.service'
import { logLevelFor } from '../../../src/libs/utils/error.middleware.utils'
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  GatewayTimeoutError,
  InternalServerError,
  NotFoundError,
  PaymentRequiredError,
  ServiceUnavailableError,
  TooManyRequestsError,
  UnprocessableEntityError,
} from '../../../src/libs/errors/http.errors'

describe('handleBackendError', () => {
  describe('a 4xx keeps the service\'s own words', () => {
    it('passes a 400 through', () => {
      const result = handleBackendError(
        { statusCode: 400, data: { detail: 'Pick at least one folder to sync.' } },
        'create connector',
      )
      expect(result).to.be.instanceOf(BadRequestError)
      expect(result.message).to.equal('Pick at least one folder to sync.')
    })

    it('passes a 403 through', () => {
      const result = handleBackendError(
        { statusCode: 403, data: { reason: 'You can only edit collections you own.' } },
        'rename collection',
      )
      expect(result).to.be.instanceOf(ForbiddenError)
      expect(result.message).to.equal('You can only edit collections you own.')
    })

    it('passes a 402 through with only its message', () => {
      const message = 'Payment required by the upstream service'
      const result = handleBackendError(
        { response: { status: 402, data: { detail: { code: 'PAYMENT_REQUIRED', message } } } },
        'Chat Stream',
      )
      expect(result).to.be.instanceOf(PaymentRequiredError)
      expect((result as PaymentRequiredError).statusCode).to.equal(402)
      expect(result.message).to.equal(message)
    })

    it('logs a 402 as a warning, and the error middleware does too', () => {
      const warn = sinon.stub(Logger.prototype, 'warn')
      const error = sinon.stub(Logger.prototype, 'error')
      try {
        const result = handleBackendError(
          {
            response: {
              status: 402,
              data: { detail: { code: 'PAYMENT_REQUIRED', message: 'Payment required by the upstream service' } },
            },
          },
          'Chat Stream',
        )
        expect(logLevelFor(result)).to.equal('warn')
        expect(warn.calledWith('Backend error during Chat Stream')).to.equal(true)
        expect(error.called).to.equal(false)
      } finally {
        sinon.restore()
      }
    })

    it('keeps the status of an answer whose message says "fetch failed"', () => {
      const answered = Object.assign(new Error('Payment check: fetch failed'), {
        response: { status: 402, data: { detail: { code: 'PAYMENT_REQUIRED', message: 'Payment check: fetch failed' } } },
      })
      const result = handleBackendError(answered, 'Chat Stream')
      expect(result).to.be.instanceOf(PaymentRequiredError)
      expect(result.message).to.equal('Payment check: fetch failed')
    })

    it('logs any other 4xx it passes on as a warning too', () => {
      const warn = sinon.stub(Logger.prototype, 'warn')
      const error = sinon.stub(Logger.prototype, 'error')
      try {
        const result = handleBackendError(
          { statusCode: 400, data: { detail: 'Pick at least one folder to sync.' } },
          'create connector',
        )
        expect(logLevelFor(result)).to.equal('warn')
        expect(warn.calledWith('Backend error during create connector')).to.equal(true)
        expect(error.called).to.equal(false)
      } finally {
        sinon.restore()
      }
    })

    it('reads an axios-shaped 404', () => {
      const result = handleBackendError(
        { response: { status: 404, data: { message: 'That team no longer exists.' } } },
        'get team',
      )
      expect(result).to.be.instanceOf(NotFoundError)
      expect(result.message).to.equal('That team no longer exists.')
    })
  })

  describe('a validation array never reads as "[object Object]"', () => {
    it('joins the messages a FastAPI 422 sends', () => {
      const result = handleBackendError(
        {
          statusCode: 422,
          data: {
            detail: [
              { loc: ['body', 'name'], msg: 'Name is required', type: 'missing' },
              { loc: ['body', 'url'], msg: 'URL must start with https', type: 'value_error' },
            ],
          },
        },
        'create connector',
      )
      expect(result).to.be.instanceOf(UnprocessableEntityError)
      expect(result.message).to.equal('Name is required; URL must start with https')
      expect(result.message).to.not.include('[object Object]')
    })

    it('does the same for the axios shape, which used to render objects', () => {
      const result = handleBackendError(
        {
          response: {
            status: 422,
            data: { detail: [{ msg: 'Name is required' }] },
          },
        },
        'create agent',
      )
      expect(result.message).to.equal('Name is required')
      expect(result.message).to.not.include('[object Object]')
    })
  })

  describe('a 5xx never repeats what the service said', () => {
    it('tells the reader what failed and what to do', () => {
      const result = handleBackendError(
        {
          statusCode: 500,
          data: { detail: "KeyError: 'llm' in qdrant_client.upsert" },
        },
        'create team',
      )
      expect(result).to.be.instanceOf(InternalServerError)
      expect(result.message).to.equal(
        'Something went wrong while PipesHub tried to create team. Please try again in a moment; if it keeps happening, ask your admin to check the services page.',
      )
      expect(result.message).to.not.include('KeyError')
      expect(result.message).to.not.include('qdrant')
    })

    it('lower-cases a capitalised operation but leaves an initialism alone', () => {
      expect(handleBackendError({ statusCode: 500, data: {} }, 'Get Agent').message).to.include(
        'tried to get Agent',
      )
      expect(handleBackendError({ statusCode: 500, data: {} }, 'AI Model Usage').message).to.include(
        'tried to AI Model Usage',
      )
    })
  })

  describe('busy or slow', () => {
    it('keeps a 429 retryable and quotes the wait', () => {
      const result = handleBackendError(
        { statusCode: 429, data: {}, headers: { 'retry-after': '5' } },
        'search',
      )
      expect(result).to.be.instanceOf(TooManyRequestsError)
      expect(result.message).to.include('try again in 5 seconds')
    })

    it('takes Retry-After from an axios response, where it actually lives', () => {
      const result = handleBackendError(
        {
          response: {
            status: 503,
            data: {},
            headers: { 'retry-after': '7' },
          },
        },
        'search',
      ) as ServiceUnavailableError
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result.metadata).to.deep.equal({ retryAfter: '7' })
      expect(result.message).to.include('try again in 7 seconds')
    })

    it("keeps a 503 sentence that was written for the person", () => {
      const result = handleBackendError(
        {
          statusCode: 503,
          data: {
            detail:
              "We couldn't confirm your sign-in just now. Please try again in a few seconds.",
          },
        },
        'upload file',
      )
      expect(result.message).to.equal(
        "We couldn't confirm your sign-in just now. Please try again in a few seconds.",
      )
    })

    it("keeps Python's 503 for an unreadable user record", () => {
      const detail =
        "We couldn't reach PipesHub's database just now. Please try again in a few seconds."
      const result = handleBackendError({ statusCode: 503, data: { detail } }, 'get team')
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result.message).to.equal(detail)
    })

    // An unknown 5xx message is replaced however readable it looks: guessing
    // from shape is what let topology through.
    for (const [label, detail] of [
      ['a service name and address', 'connector-service connection refused at 10.0.0.4'],
      ['a store name', 'Qdrant connection refused'],
      ['a stack fragment', 'at DataSourceProcessor.onNewRecords (/app/src/processor.py:214)'],
      [
        'a connection string',
        'MongoServerError: connection refused to mongodb://pipeshub:hunter2@mongo-0.internal:27017/records',
      ],
      ['plain prose we never wrote', 'The upstream pipeline stalled while draining the queue.'],
    ] as [string, string][]) {
      it(`replaces a 503 carrying ${label}`, () => {
        const result = handleBackendError(
          { statusCode: 503, data: { detail } },
          'search',
        )
        expect(result.message).to.include('briefly unavailable')
        expect(result.message).to.not.include(detail)
      })
    }

    it('maps a 504 to a gateway timeout', () => {
      const result = handleBackendError({ statusCode: 504, data: {} }, 'search')
      expect(result).to.be.instanceOf(GatewayTimeoutError)
    })

    it('reads a request that timed out as a gateway timeout, not a failure', () => {
      const result = handleBackendError(new DOMException('The operation was aborted due to timeout', 'TimeoutError'), 'list MCP servers')
      expect(result).to.be.instanceOf(GatewayTimeoutError)
      expect(result.message).to.not.include('aborted')
    })
  })

  describe('when the service cannot be reached', () => {
    it('never blames the reader\'s network', () => {
      const error = new Error('fetch failed') as Error & { cause?: { code: string } }
      error.cause = { code: 'ECONNREFUSED' }
      const result = handleBackendError(error, 'list connectors')
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result.message).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
      expect(result.message).to.not.match(/your (network|internet) connection/i)
    })

    it('says the same when the request got no response at all', () => {
      const result = handleBackendError({ request: {} }, 'list connectors')
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result.message).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
    })
  })

  describe('a 409 from a knowledge base being deleted', () => {
    // kb_service.py KB_BEING_DELETED_REASON: the connector service words this refusal the same way everywhere.
    const PYTHON_WORDING = "This knowledge base is being deleted, so it can't be changed."

    it('carries its own error code, so a client can tell it from other conflicts', () => {
      expect(KB_BEING_DELETED_MESSAGE).to.equal(PYTHON_WORDING)
      const result = handleBackendError(
        { statusCode: 409, data: { detail: PYTHON_WORDING } },
        'update collection',
      ) as any
      expect(result.statusCode).to.equal(409)
      expect(result.code).to.equal('HTTP_KB_BEING_DELETED')
      expect(result.message).to.equal(PYTHON_WORDING)
    })

    it('leaves any other 409 as a conflict (the control)', () => {
      const result = handleBackendError(
        { statusCode: 409, data: { detail: "Folder 'a' already exists in KB root" } },
        'create folder',
      )
      expect(result).to.be.instanceOf(ConflictError)
      expect((result as ConflictError).code).to.equal('HTTP_CONFLICT')
    })
  })

  it('leaves an error we already mapped alone', () => {
    const already = new NotFoundError('That collection was deleted.')
    expect(handleBackendError(already, 'get collection')).to.equal(already)
  })

  it('reads a bare validation detail with no status', () => {
    const result = handleBackendError({ detail: 'Name is required' }, 'create agent')
    expect(result).to.be.instanceOf(BadRequestError)
    expect(result.message).to.equal('Name is required')
  })

  // A service refusing the request is answered to the caller as the same 4xx;
  // logging each at error buried the real failures in the error log.
  describe('log level', () => {
    let warn: sinon.SinonStub
    let error: sinon.SinonStub

    // On the prototype: backend-error keeps the instance it got at load, which
    // another test may since have replaced as the singleton.
    beforeEach(() => {
      warn = sinon.stub(Logger.prototype, 'warn')
      error = sinon.stub(Logger.prototype, 'error')
    })

    afterEach(() => sinon.restore())

    for (const statusCode of [400, 401, 403, 404, 409, 422]) {
      it(`logs a ${String(statusCode)} from the service at warn`, () => {
        handleBackendError({ statusCode, data: { detail: 'refused' } }, 'create connector')

        expect(warn.calledOnce).to.be.true
        expect(warn.firstCall.args[0]).to.equal('Backend error during create connector')
        expect(error.called).to.be.false
      })
    }

    // Answered to the caller as a 500, so it is ours to look into.
    it('logs a 4xx it does not pass on (405) at error, with the service detail', () => {
      const result = handleBackendError({ statusCode: 405, data: { detail: 'Method Not Allowed' } }, 'create connector')

      expect(result).to.be.instanceOf(InternalServerError)
      expect(error.calledOnce).to.be.true
      expect(error.firstCall.args[1]).to.include({ statusCode: 405, errorDetail: 'Method Not Allowed' })
      expect(warn.called).to.be.false
    })

    it('logs a 429 it passes on as a 429 at warn', () => {
      handleBackendError({ statusCode: 429, data: { detail: 'slow down' } }, 'create connector')

      expect(warn.calledOnce).to.be.true
      expect(error.called).to.be.false
    })

    for (const statusCode of [500, 502]) {
      it(`logs a ${String(statusCode)} from the service at error`, () => {
        handleBackendError({ statusCode, data: { detail: 'broke' } }, 'create connector')

        expect(error.calledOnce).to.be.true
        expect(warn.called).to.be.false
      })
    }
  })
})

describe('unansweredCallError', () => {
  const withCode = (code: string) =>
    Object.assign(new TypeError('fetch failed'), { cause: Object.assign(new Error(code), { code }) })

  beforeEach(() => {
    sinon.stub(Logger.prototype, 'error')
  })

  afterEach(() => {
    sinon.restore()
  })

  for (const code of ['ECONNREFUSED', 'ENOTFOUND', 'ECONNRESET', 'EPIPE']) {
    it(`is a 503 with the shared message when fetch failed with ${code}`, () => {
      const result = unansweredCallError(withCode(code), 'Search')
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result!.message).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
    })
  }

  it("is a 503 for axios's request that got no response", () => {
    const result = unansweredCallError({ code: 'ECONNREFUSED', request: {} }, 'Get user')
    expect(result).to.be.instanceOf(ServiceUnavailableError)
  })

  it("is a 503 for axios's network failure and a broken pipe", () => {
    expect(unansweredCallError({ code: 'ERR_NETWORK', request: {} }, 'Get user')).to.be.instanceOf(ServiceUnavailableError)
    expect(unansweredCallError({ code: 'EPIPE', request: {} }, 'Get user')).to.be.instanceOf(ServiceUnavailableError)
  })

  for (const code of ['ECONNABORTED', 'ETIMEDOUT']) {
    it(`is a 504 for axios's own timeout (${code})`, () => {
      expect(unansweredCallError({ code, request: {} }, 'Get user')).to.be.instanceOf(GatewayTimeoutError)
    })
  }

  it('is null for an axios timeout code on a call the service answered', () => {
    expect(unansweredCallError({ code: 'ECONNABORTED', request: {}, response: { status: 502 } }, 'Get user')).to.equal(null)
  })

  it('stays a 503 when fetch timed out connecting (ETIMEDOUT as the cause)', () => {
    expect(unansweredCallError(withCode('ETIMEDOUT'), 'Search')).to.be.instanceOf(ServiceUnavailableError)
  })

  it('is null for an axios request that failed on a certificate', () => {
    expect(unansweredCallError({ code: 'ERR_TLS_CERT_ALTNAME_INVALID', request: {} }, 'Get user')).to.equal(null)
    expect(unansweredCallError({ code: 'CERT_HAS_EXPIRED', request: {} }, 'Get user')).to.equal(null)
  })

  it('is a 504 for a timed-out call', () => {
    const result = unansweredCallError(new DOMException('timed out', 'TimeoutError'), 'Search')
    expect(result).to.be.instanceOf(GatewayTimeoutError)
  })

  it('is null for a call the service answered, whatever its words', () => {
    expect(unansweredCallError({ statusCode: 502, message: 'fetch failed' }, 'Search')).to.equal(null)
    expect(unansweredCallError({ response: { status: 500 }, request: {} }, 'Search')).to.equal(null)
  })

  it('is null for a request without response that carries a validation detail', () => {
    expect(unansweredCallError({ request: {}, detail: 'name is required' }, 'Create')).to.equal(null)
  })

  it('is null for a fetch that failed for a reason retrying will not fix', () => {
    expect(unansweredCallError(withCode('ERR_TLS_CERT_ALTNAME_INVALID'), 'Search')).to.equal(null)
    expect(unansweredCallError(withCode('UND_ERR_REQ_CONTENT_LENGTH_MISMATCH'), 'Search')).to.equal(null)
  })

  it('is null for anything that is not a failed call', () => {
    expect(unansweredCallError(new Error('boom'), 'Search')).to.equal(null)
    expect(unansweredCallError(new DOMException('aborted', 'AbortError'), 'Search')).to.equal(null)
    expect(unansweredCallError(null, 'Search')).to.equal(null)
    expect(unansweredCallError('fetch failed', 'Search')).to.equal(null)
  })

  it('logs the cause code', () => {
    unansweredCallError(withCode('ECONNREFUSED'), 'Get user teams')
    const logged = (Logger.prototype.error as sinon.SinonStub).firstCall
    expect(logged.args[0]).to.equal('Could not reach the service during Get user teams')
    expect(logged.args[1]).to.deep.equal({ cause: 'ECONNREFUSED' })
  })

  it('gives handleBackendError the same answer', () => {
    expect(handleBackendError(withCode('ECONNREFUSED'), 'Search')).to.be.instanceOf(ServiceUnavailableError)
    expect(handleBackendError({ code: 'ECONNREFUSED', request: {} }, 'Search')).to.be.instanceOf(ServiceUnavailableError)
    expect(handleBackendError(new DOMException('timed out', 'TimeoutError'), 'Search')).to.be.instanceOf(GatewayTimeoutError)
  })
})
