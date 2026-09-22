import { expect } from 'chai'
import {
  handleBackendError,
  SERVICE_UNAVAILABLE_MESSAGE,
} from '../../../src/libs/errors/backend-error'
import {
  BadRequestError,
  ForbiddenError,
  GatewayTimeoutError,
  InternalServerError,
  NotFoundError,
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

  it('leaves an error we already mapped alone', () => {
    const already = new NotFoundError('That collection was deleted.')
    expect(handleBackendError(already, 'get collection')).to.equal(already)
  })

  it('reads a bare validation detail with no status', () => {
    const result = handleBackendError({ detail: 'Name is required' }, 'create agent')
    expect(result).to.be.instanceOf(BadRequestError)
    expect(result.message).to.equal('Name is required')
  })
})
