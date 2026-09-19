import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  handleBackendError,
  retryAfterToSeconds,
  handleConnectorResponse,
} from '../../../../src/modules/tokens_manager/utils/connector.utils'
import {
  BadRequestError,
  UnauthorizedError,
  ForbiddenError,
  NotFoundError,
  ConflictError,
  InternalServerError,
  ServiceUnavailableError,
  GatewayTimeoutError,
  TooManyRequestsError,
} from '../../../../src/libs/errors/http.errors'

describe('tokens_manager/utils/connector.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('handleBackendError', () => {
    it('should return ServiceUnavailableError for ECONNREFUSED', () => {
      const error = { cause: { code: 'ECONNREFUSED' } }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(ServiceUnavailableError)
    })

    it('should return ServiceUnavailableError for fetch failed message', () => {
      const error = { message: 'fetch failed' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(ServiceUnavailableError)
    })

    it('should return BadRequestError for status 400', () => {
      const error = { statusCode: 400, data: { detail: 'bad input' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
    })

    it('should return UnauthorizedError for status 401', () => {
      const error = { statusCode: 401, data: { detail: 'unauthorized' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(UnauthorizedError)
    })

    it('should return ForbiddenError for status 403', () => {
      const error = { statusCode: 403, data: { detail: 'forbidden' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(ForbiddenError)
    })

    it('should return NotFoundError for status 404', () => {
      const error = { statusCode: 404, data: { detail: 'not found' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(NotFoundError)
    })

    it('should return ConflictError for status 409', () => {
      const error = { statusCode: 409, data: { detail: 'conflict' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(ConflictError)
    })

    it('should return InternalServerError for status 500', () => {
      const error = { statusCode: 500, data: { detail: 'server error' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(InternalServerError)
    })

    it('should return BadRequestError for status 422', () => {
      const error = { statusCode: 422, data: { detail: 'validation error' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
    })

    it('should keep a 503 as ServiceUnavailableError with the upstream Retry-After', () => {
      const error = {
        statusCode: 503,
        data: { detail: "We couldn't confirm your sign-in just now. Please try again in a few seconds." },
        headers: { 'retry-after': '5' },
        message: '',
      }
      const result = handleBackendError(error, 'test operation') as ServiceUnavailableError
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result.message).to.equal("We couldn't confirm your sign-in just now. Please try again in a few seconds.")
      expect(result.metadata).to.deep.equal({ retryAfter: '5' })
    })

    it('should keep a 503 without Retry-After as ServiceUnavailableError', () => {
      const error = { statusCode: 503, data: { detail: 'busy' }, message: '' }
      const result = handleBackendError(error, 'test operation') as ServiceUnavailableError
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result.metadata).to.be.undefined
    })

    it('should keep a 504 as GatewayTimeoutError', () => {
      const error = { statusCode: 504, data: { detail: 'timed out' }, message: '' }
      expect(handleBackendError(error, 'test operation')).to.be.instanceOf(GatewayTimeoutError)
    })

    it('should keep a 429 as TooManyRequestsError', () => {
      const error = { statusCode: 429, data: { detail: 'slow down' }, headers: { 'retry-after': '30' }, message: '' }
      const result = handleBackendError(error, 'test operation') as TooManyRequestsError
      expect(result).to.be.instanceOf(TooManyRequestsError)
      expect(result.metadata).to.deep.equal({ retryAfter: '30' })
    })

    it('should give a busy 503 with no detail a plain retry hint, not an axios message', () => {
      const error = { statusCode: 503, data: {}, headers: { 'retry-after': '5' }, message: 'Request failed with status code 503' }
      const result = handleBackendError(error, 'test operation') as ServiceUnavailableError
      expect(result).to.be.instanceOf(ServiceUnavailableError)
      expect(result.message).to.equal(
        'This part of PipesHub is briefly unavailable. Please try again in 5 seconds.',
      )
    })

    it('should give a 429 and a 504 with no detail a plain message', () => {
      const busy = handleBackendError({ statusCode: 429, data: {}, message: '' }, 'test operation')
      expect(busy.message).to.equal(
        'PipesHub is handling a lot of requests right now. Please try again in a few seconds.',
      )
      const slow = handleBackendError({ statusCode: 504, data: undefined, message: '' }, 'test operation')
      expect(slow.message).to.equal(
        'This took longer than expected to respond. Please try again in a few seconds.',
      )
    })

    it('should turn a future HTTP-date Retry-After into the seconds left', () => {
      const inTen = new Date(Date.now() + 9_500).toUTCString()
      const result = handleBackendError(
        { statusCode: 503, data: {}, headers: { 'retry-after': inTen }, message: '' },
        'test operation',
      )
      expect(result.message).to.match(/^This part of PipesHub is briefly unavailable\. Please try again in (9|10) seconds\.$/)
    })

    it('should fall back to "a few seconds" for a past or invalid Retry-After', () => {
      const past = new Date(Date.now() - 60_000).toUTCString()
      for (const header of [past, 'soon', '0', '600']) {
        const result = handleBackendError(
          { statusCode: 503, data: {}, headers: { 'retry-after': header }, message: '' },
          'test operation',
        )
        expect(result.message).to.equal(
          'This part of PipesHub is briefly unavailable. Please try again in a few seconds.',
        )
      }
    })

    it('should parse Retry-After seconds and dates against a fixed clock', () => {
      const now = Date.parse('Wed, 21 Oct 2026 07:28:00 GMT')
      expect(retryAfterToSeconds('7', now)).to.equal(7)
      expect(retryAfterToSeconds('Wed, 21 Oct 2026 07:28:04 GMT', now)).to.equal(4)
      expect(retryAfterToSeconds('Wed, 21 Oct 2026 07:27:00 GMT', now)).to.be.undefined
      expect(retryAfterToSeconds('not a date', now)).to.be.undefined
      expect(retryAfterToSeconds(undefined, now)).to.be.undefined
    })

    it('should return an already-mapped HTTP error unchanged', () => {
      const mapped = new ServiceUnavailableError('busy', { retryAfter: '5' })
      expect(handleBackendError(mapped, 'test operation')).to.equal(mapped)
    })

    it('should return InternalServerError for unknown status codes', () => {
      const error = { statusCode: 999, data: { detail: 'unknown' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(InternalServerError)
    })

    it('should throw ServiceUnavailableError for ECONNREFUSED in errorDetail', () => {
      const error = { statusCode: undefined, data: { detail: 'ECONNREFUSED' }, message: '' }
      expect(() => handleBackendError(error, 'test operation')).to.throw(ServiceUnavailableError)
    })

    it('should use data.reason as fallback error detail', () => {
      const error = { statusCode: 400, data: { reason: 'bad reason' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
    })

    it('should stringify FastAPI validation error array (422)', () => {
      const error = {
        statusCode: 422,
        data: {
          detail: [
            { loc: ['body', 'field1'], msg: 'Field is required', type: 'value_error.missing' },
            { loc: ['body', 'field2'], msg: 'Invalid type', type: 'type_error.integer' },
          ],
        },
        message: '',
      }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
      expect(result.message).to.include('Field is required')
      expect(result.message).to.include('Invalid type')
    })

    it('should handle detail as array of objects without msg property', () => {
      const error = {
        statusCode: 422,
        data: {
          detail: [
            { loc: ['body', 'field1'], type: 'value_error' },
            { something: 'else' },
          ],
        },
        message: '',
      }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
      // Should stringify the objects
      expect(result.message).to.be.a('string')
    })

    it('should handle detail as object', () => {
      const error = {
        statusCode: 400,
        data: { detail: { error: 'complex error', code: 123 } },
        message: '',
      }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
      expect(result.message).to.include('error')
      expect(result.message).to.include('complex error')
    })

    it('should handle detail as primitive string', () => {
      const error = { statusCode: 400, data: { detail: 'simple string error' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
      expect(result.message).to.equal('simple string error')
    })

    it('should handle missing detail gracefully', () => {
      const error = { statusCode: 400, data: {}, message: 'fallback message' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
      expect(result.message).to.equal('fallback message')
    })

    it('should default to "Unknown error" when all detail sources are missing', () => {
      const error = { statusCode: 400, data: {}, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
      expect(result.message).to.equal('Unknown error')
    })
  })

  describe('handleConnectorResponse', () => {
    it('should return success response with data', () => {
      const res: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub(),
      }
      const connectorResponse = { statusCode: 200, data: { foo: 'bar' } }

      handleConnectorResponse(connectorResponse, res, 'Test op', 'Not found')

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ foo: 'bar' })).to.be.true
    })

    it('should throw when status code is not 2xx', () => {
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
      const connectorResponse = { statusCode: 404, data: { detail: 'not found' } }

      expect(() =>
        handleConnectorResponse(connectorResponse, res, 'Test op', 'Not found'),
      ).to.throw()
    })

    it('should throw NotFoundError when data is missing', () => {
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
      const connectorResponse = { statusCode: 200, data: null }

      expect(() =>
        handleConnectorResponse(connectorResponse, res, 'Test op', 'Not found'),
      ).to.throw(NotFoundError)
    })
  })
})
