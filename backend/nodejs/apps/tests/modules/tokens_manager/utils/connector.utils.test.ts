import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  handleBackendError,
  handleConnectorResponse,
  annotateLocalFsDesktopPresence,
  respondLocalFsDesktopRefusal,
  DESKTOP_OFFLINE_CODE,
  DESKTOP_UNCLAIMED_CODE,
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

    it('should return BadRequestError for status 422', () => {
      const error = { statusCode: 422, data: { detail: 'validation error' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(BadRequestError)
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

describe('tokens_manager/utils/connector.utils - Local FS desktop presence', () => {
  describe('annotateLocalFsDesktopPresence', () => {
    const presence = () => ({
      isLocalFsDesktopOnline: sinon.stub(),
      isDesktopConnected: sinon.stub().returns(null),
    })

    it('stamps desktopOnline on a single Local FS connector keyed by its owner', () => {
      const p = presence()
      p.isLocalFsDesktopOnline.returns(false)
      const body = { success: true, connector: { _key: 'c1', type: 'Local FS', createdBy: 'owner-1', isActive: true } }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connector).to.have.property('desktopOnline', false)
      expect(p.isLocalFsDesktopOnline.calledOnceWithExactly('org-1', 'owner-1', 'c1')).to.be.true
    })

    it('stamps only the Local FS rows of a list', () => {
      const p = presence()
      p.isLocalFsDesktopOnline.returns(true)
      const body = {
        connectors: [
          { _key: 'c1', type: 'Local FS', createdBy: 'owner-1', isActive: true },
          { _key: 'c2', type: 'Slack', createdBy: 'owner-1', isActive: true },
          { _key: 'c3', type: 'local_fs', createdBy: 'owner-2', isActive: true },
        ],
      }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connectors[0]).to.have.property('desktopOnline', true)
      expect(body.connectors[1]).to.not.have.property('desktopOnline')
      expect(body.connectors[2]).to.have.property('desktopOnline', true)
      expect(p.isLocalFsDesktopOnline.calledTwice).to.be.true
    })

    it('omits the field when presence is unknown', () => {
      const p = presence()
      p.isLocalFsDesktopOnline.returns(null)
      const body = { connector: { _key: 'c1', type: 'Local FS', createdBy: 'owner-1', isActive: true } }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connector).to.not.have.property('desktopOnline')
    })

    it('skips connectors whose sync is not enabled: no claim is expected yet', () => {
      const p = presence()
      p.isLocalFsDesktopOnline.returns(false)
      const body = { connector: { _key: 'c1', type: 'Local FS', createdBy: 'owner-1', isActive: false } }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connector).to.not.have.property('desktopOnline')
      expect(p.isLocalFsDesktopOnline.called).to.be.false
    })

    it('is a no-op without presence, orgId, owner, or a body', () => {
      const p = presence()
      p.isLocalFsDesktopOnline.returns(false)
      const noOwner = { connector: { _key: 'c1', type: 'Local FS', isActive: true } }

      annotateLocalFsDesktopPresence(noOwner, 'org-1', p)
      annotateLocalFsDesktopPresence({ connector: { _key: 'c1', type: 'Local FS', createdBy: 'o', isActive: true } }, undefined, p)
      annotateLocalFsDesktopPresence({ connector: { _key: 'c1', type: 'Local FS', createdBy: 'o', isActive: true } }, 'org-1', null)
      annotateLocalFsDesktopPresence(null, 'org-1', p)
      annotateLocalFsDesktopPresence('text', 'org-1', p)

      expect(noOwner.connector).to.not.have.property('desktopOnline')
      expect(p.isLocalFsDesktopOnline.called).to.be.false
    })
  })

  describe('respondLocalFsDesktopRefusal', () => {
    it('writes a 409 whose details.code the frontend can match', () => {
      const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }

      respondLocalFsDesktopRefusal(res as any, 'c1')

      expect(res.status.calledOnceWith(409)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.success).to.equal(false)
      expect(body.code).to.equal(DESKTOP_OFFLINE_CODE)
      expect(body.details).to.deep.include({ code: DESKTOP_OFFLINE_CODE, connectorId: 'c1' })
      expect(body.message).to.be.a('string').and.not.empty
    })

    it('uses the unclaimed code and first-enable wording for that reason', () => {
      const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }

      respondLocalFsDesktopRefusal(res as any, 'c1', 'unclaimed')

      expect(res.status.calledOnceWith(409)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.code).to.equal(DESKTOP_UNCLAIMED_CODE)
      expect(body.details.code).to.equal(DESKTOP_UNCLAIMED_CODE)
      expect(body.message).to.include('enable sync there once')
    })
  })
})
