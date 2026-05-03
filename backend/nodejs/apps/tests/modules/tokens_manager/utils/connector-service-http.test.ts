import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  handleBackendError,
  handleConnectorResponse,
} from '../../../../src/modules/tokens_manager/utils/connector-service-http'
import {
  BadRequestError,
  UnauthorizedError,
  ForbiddenError,
  NotFoundError,
  ConflictError,
  InternalServerError,
  ServiceUnavailableError,
} from '../../../../src/libs/errors/http.errors'

describe('tokens_manager/utils/connector-service-http', () => {
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
})
