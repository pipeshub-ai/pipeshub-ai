/**
 * Unit tests for OAuthDcrController.
 *
 * Controller is intentionally slim — its responsibility is marshalling +
 * RFC 7591 §3.2.2 error mapping. We verify each thin edge:
 *   - 201 on register, with no-store cache header
 *   - 200 on RFC 7592 GET / PUT
 *   - 204 on RFC 7592 DELETE
 *   - InvalidRedirectUriError -> 400 invalid_redirect_uri
 *   - InvalidScopeError -> 400 invalid_client_metadata
 *   - BadRequestError("invalid_client_metadata: …") -> error code split out
 *   - Unknown errors fall through to next() for the global error middleware.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OAuthDcrController } from '../../../../src/modules/oauth_provider/controller/oauth.dcr.controller'
import { BadRequestError, NotFoundError } from '../../../../src/libs/errors/http.errors'
import {
  InvalidRedirectUriError,
  InvalidScopeError,
} from '../../../../src/libs/errors/oauth.errors'

function buildRes() {
  const res: any = {
    statusCode: 200,
    body: undefined,
    headers: {} as Record<string, string>,
  }
  res.status = sinon.stub().callsFake((code: number) => {
    res.statusCode = code
    return res
  })
  res.json = sinon.stub().callsFake((b: unknown) => {
    res.body = b
    return res
  })
  res.send = sinon.stub().callsFake((b: unknown) => {
    res.body = b
    return res
  })
  res.setHeader = sinon.stub().callsFake((k: string, v: string) => {
    res.headers[k] = v
  })
  return res
}

describe('OAuthDcrController', () => {
  let controller: OAuthDcrController
  let mockLogger: any
  let mockService: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
    mockService = {
      register: sinon.stub(),
      getRegistrationMetadata: sinon.stub(),
      updateRegistration: sinon.stub(),
      deleteRegistration: sinon.stub(),
    }
    controller = new OAuthDcrController(mockLogger, mockService)
  })

  afterEach(() => sinon.restore())

  describe('register', () => {
    it('returns 201 + sets no-store cache headers on success', async () => {
      mockService.register.resolves({ client_id: 'cid', client_id_issued_at: 1 })
      const req: any = { body: { client_name: 'x', redirect_uris: ['https://e.com/cb'] } }
      const res = buildRes()
      await controller.register(req, res, sinon.stub())
      expect(res.statusCode).to.equal(201)
      expect(res.body).to.have.property('client_id', 'cid')
      expect(res.headers['Cache-Control']).to.equal('no-store')
      expect(res.headers['Pragma']).to.equal('no-cache')
    })

    it('maps InvalidRedirectUriError -> 400 invalid_redirect_uri', async () => {
      mockService.register.rejects(new InvalidRedirectUriError('bad uri'))
      const next = sinon.stub()
      const res = buildRes()
      await controller.register({ body: {} } as any, res, next as any)
      expect(res.statusCode).to.equal(400)
      expect(res.body).to.deep.equal({
        error: 'invalid_redirect_uri',
        error_description: 'bad uri',
      })
      expect(next.called).to.be.false
    })

    it('maps InvalidScopeError -> 400 invalid_client_metadata', async () => {
      mockService.register.rejects(new InvalidScopeError('disallowed'))
      const res = buildRes()
      await controller.register({ body: {} } as any, res, sinon.stub() as any)
      expect(res.statusCode).to.equal(400)
      expect(res.body.error).to.equal('invalid_client_metadata')
    })

    it('maps BadRequestError to invalid_client_metadata without string splitting', async () => {
      mockService.register.rejects(
        new BadRequestError(
          'invalid_client_metadata: client_credentials grant is not permitted for dynamically registered clients',
        ),
      )
      const res = buildRes()
      await controller.register({ body: {} } as any, res, sinon.stub() as any)
      expect(res.statusCode).to.equal(400)
      expect(res.body).to.deep.equal({
        error: 'invalid_client_metadata',
        error_description:
          'invalid_client_metadata: client_credentials grant is not permitted for dynamically registered clients',
      })
    })

    it('falls through to next() for unknown errors', async () => {
      mockService.register.rejects(new Error('boom'))
      const next = sinon.stub()
      const res = buildRes()
      await controller.register({ body: {} } as any, res, next as any)
      expect(next.calledOnce).to.be.true
      expect((next.firstCall.args[0] as Error).message).to.equal('boom')
    })
  })

  describe('getRegistration / updateRegistration / deleteRegistration', () => {
    it('GET returns 200 + body from service', async () => {
      mockService.getRegistrationMetadata.returns({ client_id: 'cid' })
      const req: any = { oauthApp: { clientId: 'cid' } }
      const res = buildRes()
      await controller.getRegistration(req, res, sinon.stub() as any)
      expect(res.body).to.have.property('client_id', 'cid')
      expect(res.headers['Cache-Control']).to.equal('no-store')
    })

    it('PUT returns 200 + updated body', async () => {
      mockService.updateRegistration.resolves({ client_id: 'cid', client_name: 'x' })
      const req: any = { oauthApp: { clientId: 'cid' }, body: { client_name: 'x' } }
      const res = buildRes()
      await controller.updateRegistration(req, res, sinon.stub() as any)
      expect(res.body).to.deep.include({ client_id: 'cid', client_name: 'x' })
    })

    it('DELETE returns 204 with empty body', async () => {
      mockService.deleteRegistration.resolves()
      const req: any = { oauthApp: { clientId: 'cid' } }
      const res = buildRes()
      await controller.deleteRegistration(req, res, sinon.stub() as any)
      expect(res.statusCode).to.equal(204)
      expect(res.send.calledOnce).to.be.true
    })

    it('GET maps NotFoundError -> 404 invalid_client_metadata', async () => {
      mockService.getRegistrationMetadata.callsFake(() => {
        throw new NotFoundError('gone')
      })
      const res = buildRes()
      await controller.getRegistration({ oauthApp: {} } as any, res, sinon.stub() as any)
      expect(res.statusCode).to.equal(404)
      expect(res.body.error).to.equal('invalid_client_metadata')
    })
  })
})
