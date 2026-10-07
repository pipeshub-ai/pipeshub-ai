import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Response } from 'express'
import {
  handleBackendError,
  retryAfterToSeconds,
  handleConnectorResponse,
  annotateLocalFsDesktopPresence,
  respondLocalFsDesktopRefusal,
  localFsRefusalFromBackend,
  DESKTOP_OFFLINE_CODE,
  DESKTOP_OWNED_BY_OTHER_DEVICE_CODE,
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
  GatewayTimeoutError,
  TooManyRequestsError,
  UnprocessableEntityError,
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

    it('should keep status 422 as UnprocessableEntityError', () => {
      const error = { statusCode: 422, data: { detail: 'validation error' }, message: '' }
      const result = handleBackendError(error, 'test operation')
      expect(result).to.be.instanceOf(UnprocessableEntityError)
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

    it('should return ServiceUnavailableError for ECONNREFUSED in errorDetail', () => {
      const error = { statusCode: 500, data: { detail: 'ECONNREFUSED' }, message: '' }
      expect(handleBackendError(error, 'test operation')).to.be.instanceOf(ServiceUnavailableError)
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
      expect(result).to.be.instanceOf(UnprocessableEntityError)
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
      expect(result).to.be.instanceOf(UnprocessableEntityError)
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
      isLocalFsDeviceOnline: sinon.stub(),
      isDesktopConnected: sinon.stub().returns(null),
    })
    const localFs = (overrides: Record<string, unknown> = {}) => ({
      _key: 'c1',
      type: 'Local FS',
      createdBy: 'owner-1',
      isActive: true,
      ownerDeviceId: 'dev-a',
      ...overrides,
    })

    it('stamps desktopOnline from the owner device, keyed by createdBy', () => {
      const p = presence()
      p.isLocalFsDeviceOnline.returns(false)
      const body = { success: true, connector: localFs() }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connector).to.have.property('desktopOnline', false)
      expect(p.isLocalFsDeviceOnline.calledOnceWithExactly('org-1', 'owner-1', 'dev-a')).to.be.true
    })

    it('stamps only the Local FS rows of a list, each by its own owner device', () => {
      const p = presence()
      p.isLocalFsDeviceOnline.withArgs('org-1', 'owner-1', 'dev-a').returns(true)
      p.isLocalFsDeviceOnline.withArgs('org-1', 'owner-2', 'dev-b').returns(false)
      const body = {
        connectors: [
          localFs(),
          { _key: 'c2', type: 'Slack', createdBy: 'owner-1', isActive: true },
          localFs({ _key: 'c3', type: 'local_fs', createdBy: 'owner-2', ownerDeviceId: 'dev-b' }),
        ],
      }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connectors[0]).to.have.property('desktopOnline', true)
      expect(body.connectors[1]).to.not.have.property('desktopOnline')
      expect(body.connectors[2]).to.have.property('desktopOnline', false)
      expect(p.isLocalFsDeviceOnline.calledTwice).to.be.true
    })

    it('omits the field when presence is unknown', () => {
      const p = presence()
      p.isLocalFsDeviceOnline.returns(null)
      const body = { connector: localFs() }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connector).to.not.have.property('desktopOnline')
    })

    it('skips connectors whose sync is not enabled', () => {
      const p = presence()
      p.isLocalFsDeviceOnline.returns(false)
      const body = { connector: localFs({ isActive: false }) }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connector).to.not.have.property('desktopOnline')
      expect(p.isLocalFsDeviceOnline.called).to.be.false
    })

    it('leaves desktopOnline unset when the connector has no owner device', () => {
      const p = presence()
      p.isLocalFsDeviceOnline.returns(false)
      const body = { connector: localFs({ ownerDeviceId: null }) }

      annotateLocalFsDesktopPresence(body, 'org-1', p)

      expect(body.connector).to.not.have.property('desktopOnline')
      expect(p.isLocalFsDeviceOnline.called).to.be.false
    })

    it('is a no-op without presence, orgId, owner, or a body', () => {
      const p = presence()
      p.isLocalFsDeviceOnline.returns(false)
      const noOwner = { connector: localFs({ createdBy: undefined }) }

      annotateLocalFsDesktopPresence(noOwner, 'org-1', p)
      annotateLocalFsDesktopPresence({ connector: localFs() }, undefined, p)
      annotateLocalFsDesktopPresence({ connector: localFs() }, 'org-1', null)
      annotateLocalFsDesktopPresence(null, 'org-1', p)
      annotateLocalFsDesktopPresence('text', 'org-1', p)

      expect(noOwner.connector).to.not.have.property('desktopOnline')
      expect(p.isLocalFsDeviceOnline.called).to.be.false
    })
  })

  describe('respondLocalFsDesktopRefusal', () => {
    type LocalFsRefusalBody = {
      success: boolean
      code: string
      message: string
      details: { code: string; connectorId: string; ownerDeviceName?: string }
    }

    type LocalFsRefusalResponse = {
      status: sinon.SinonStub<[409], LocalFsRefusalResponse>
      json: sinon.SinonStub<[LocalFsRefusalBody], LocalFsRefusalResponse>
    }

    function createLocalFsRefusalRes(): LocalFsRefusalResponse {
      const res = {
        status: sinon.stub<[409], LocalFsRefusalResponse>(),
        json: sinon.stub<[LocalFsRefusalBody], LocalFsRefusalResponse>(),
      }
      res.status.returns(res)
      res.json.returns(res)
      return res
    }

    it('writes a 409 whose details.code the frontend can match', () => {
      const res = createLocalFsRefusalRes()

      respondLocalFsDesktopRefusal(res as unknown as Response, 'c1')

      expect(res.status.calledOnceWith(409)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.success).to.equal(false)
      expect(body.code).to.equal(DESKTOP_OFFLINE_CODE)
      expect(body.details).to.deep.include({ code: DESKTOP_OFFLINE_CODE, connectorId: 'c1' })
      expect(body.message).to.be.a('string').and.not.empty
    })

    it('uses the unclaimed code and first-enable wording for that reason', () => {
      const res = createLocalFsRefusalRes()

      respondLocalFsDesktopRefusal(res as unknown as Response, 'c1', 'unclaimed')

      expect(res.status.calledOnceWith(409)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.code).to.equal(DESKTOP_UNCLAIMED_CODE)
      expect(body.details.code).to.equal(DESKTOP_UNCLAIMED_CODE)
      expect(body.message).to.include('enable sync there once')
    })

    it('names the owner device when refusing another device', () => {
      const res = createLocalFsRefusalRes()

      respondLocalFsDesktopRefusal(res as unknown as Response, 'c1', 'other_device', 'Work Laptop')

      const body = res.json.firstCall.args[0]
      expect(body.code).to.equal(DESKTOP_OWNED_BY_OTHER_DEVICE_CODE)
      expect(body.details).to.deep.include({
        code: DESKTOP_OWNED_BY_OTHER_DEVICE_CODE,
        connectorId: 'c1',
        ownerDeviceName: 'Work Laptop',
      })
      expect(body.message).to.include('Work Laptop')
    })
  })

  describe('localFsRefusalFromBackend', () => {
    it('maps a Python 409 whose detail leads with a refusal code', () => {
      expect(
        localFsRefusalFromBackend({
          statusCode: 409,
          data: { detail: 'DESKTOP_OWNED_BY_OTHER_DEVICE: Connector c1 is owned by device X.' },
        }),
      ).to.equal('other_device')
      expect(
        localFsRefusalFromBackend({ statusCode: 409, data: { detail: 'DESKTOP_UNCLAIMED: no owner' } }),
      ).to.equal('unclaimed')
    })

    it('reads the code off an object-shaped detail', () => {
      expect(
        localFsRefusalFromBackend({ statusCode: 409, data: { detail: { code: 'DESKTOP_OFFLINE' } } }),
      ).to.equal('offline')
      expect(
        localFsRefusalFromBackend({ statusCode: 409, data: { detail: { code: 'HTTP_CONFLICT' } } }),
      ).to.equal(null)
      // A non-string `code` must not be coerced into a lookup.
      expect(
        localFsRefusalFromBackend({ statusCode: 409, data: { detail: { code: 42 } } }),
      ).to.equal(null)
      expect(localFsRefusalFromBackend({ statusCode: 409, data: { detail: {} } })).to.equal(null)
    })

    it('does not resolve inherited object keys to a reason', () => {
      // respondLocalFsDesktopRefusal indexes DESKTOP_REFUSAL by the reason, so
      // anything but a real reason here throws on an otherwise ordinary 409.
      expect(
        localFsRefusalFromBackend({ statusCode: 409, data: { detail: 'constructor: boom' } }),
      ).to.equal(null)
      expect(
        localFsRefusalFromBackend({ statusCode: 409, data: { detail: { code: 'toString' } } }),
      ).to.equal(null)
    })

    it('ignores other conflicts and non-409 responses', () => {
      expect(
        localFsRefusalFromBackend({
          statusCode: 409,
          data: { detail: 'A full sync is in progress. Please wait and try again.' },
        }),
      ).to.equal(null)
      expect(
        localFsRefusalFromBackend({ statusCode: 400, data: { detail: 'DESKTOP_UNCLAIMED: no owner' } }),
      ).to.equal(null)
      expect(localFsRefusalFromBackend({ statusCode: 200, data: {} })).to.equal(null)
    })
  })
})
