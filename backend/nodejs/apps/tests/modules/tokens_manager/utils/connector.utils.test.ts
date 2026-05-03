import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  getConnectorErrorLogFields,
  handleBackendError,
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

  describe('getConnectorErrorLogFields', () => {
    it('extracts message + response.status + response.data when error is an Error with attached response', () => {
      const err = Object.assign(new Error('boom'), {
        response: { status: 502, data: { detail: 'upstream' } },
      })
      const fields = getConnectorErrorLogFields(err)
      expect(fields.message).to.equal('boom')
      expect(fields.status).to.equal(502)
      expect(fields.data).to.deep.equal({ detail: 'upstream' })
    })

    it('extracts message + status + data when error is a plain record with a response object', () => {
      const fields = getConnectorErrorLogFields({
        message: 'plain',
        response: { status: 503, data: { reason: 'down' } },
      })
      expect(fields.message).to.equal('plain')
      expect(fields.status).to.equal(503)
      expect(fields.data).to.deep.equal({ reason: 'down' })
    })

    it('falls back to String(error) for non-string message on plain record', () => {
      const fields = getConnectorErrorLogFields({ message: 12345 })
      expect(fields.message).to.equal('[object Object]')
      expect(fields.status).to.be.undefined
      expect(fields.data).to.be.undefined
    })

    it('handles non-numeric status by leaving status undefined', () => {
      const fields = getConnectorErrorLogFields({
        message: 'x',
        response: { status: 'NaN' },
      })
      expect(fields.status).to.be.undefined
    })

    it('returns String(error) for primitives', () => {
      expect(getConnectorErrorLogFields('plain string').message).to.equal(
        'plain string',
      )
      expect(getConnectorErrorLogFields(42).message).to.equal('42')
    })
  })

  describe('handleBackendError — additional branches', () => {
    it('returns InternalServerError fallback when error is null', () => {
      const result = handleBackendError(null, 'op')
      expect(result).to.be.instanceOf(InternalServerError)
      expect(result.message).to.equal('op failed: unknown')
    })

    it('returns InternalServerError fallback when error is undefined', () => {
      const result = handleBackendError(undefined, 'op')
      expect(result).to.be.instanceOf(InternalServerError)
      expect(result.message).to.equal('op failed: unknown')
    })

    it('returns ServiceUnavailableError when error.message contains "fetch failed"', () => {
      const err = new Error('connect: fetch failed')
      const result = handleBackendError(err, 'op')
      expect(result).to.be.instanceOf(ServiceUnavailableError)
    })

    it('serialises non-string errorDetail via JSON.stringify', () => {
      const err = {
        statusCode: 400,
        data: { detail: { nested: 'object' } },
        message: '',
      }
      const result = handleBackendError(err, 'op')
      expect(result).to.be.instanceOf(BadRequestError)
      expect(result.message).to.equal('{"nested":"object"}')
    })

    it('falls back to data.message when detail and reason are missing', () => {
      const err = { statusCode: 400, data: { message: 'mid' }, message: 'top' }
      const result = handleBackendError(err, 'op')
      expect(result.message).to.equal('mid')
    })

    it('falls back to error.message when data has no detail/reason/message', () => {
      const err = { statusCode: 400, data: {}, message: 'top-level' }
      const result = handleBackendError(err, 'op')
      expect(result.message).to.equal('top-level')
    })

    it('uses "Unknown error" when nothing is provided', () => {
      const err = { statusCode: 400, data: {}, message: undefined }
      const result = handleBackendError(err, 'op')
      expect(result.message).to.equal('Unknown error')
    })
  })
})
