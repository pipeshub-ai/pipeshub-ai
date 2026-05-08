import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { OAuthRegistrationTokenMiddleware } from '../../../../src/modules/oauth_provider/middlewares/oauth.registration_token.middleware'
import {
  OAuthApp,
  OAuthAppRegisteredVia,
  OAuthAppStatus,
} from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'

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
  res.setHeader = sinon.stub().callsFake((k: string, v: string) => {
    res.headers[k] = v
  })
  return res
}

describe('OAuthRegistrationTokenMiddleware', () => {
  let middleware: OAuthRegistrationTokenMiddleware
  let mockTokenService: any

  beforeEach(() => {
    mockTokenService = {
      hashTokenPublic: sinon.stub(),
    }
    middleware = new OAuthRegistrationTokenMiddleware(mockTokenService)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('returns 401 when authorization header is missing', async () => {
    const req: any = { headers: {}, params: { client_id: 'cid-1' } }
    const res = buildRes()
    const next = sinon.stub()

    await middleware.authenticate(req, res, next)

    expect(res.statusCode).to.equal(401)
    expect(res.body.error).to.equal('invalid_token')
    expect(res.body.error_description).to.equal('Missing bearer token')
    expect(res.headers['WWW-Authenticate']).to.include('error="invalid_token"')
    expect(next.called).to.be.false
  })

  it('escapes backslashes and quotes in WWW-Authenticate error_description', async () => {
    const req: any = { headers: {}, params: { client_id: 'cid-1' } }
    const res = buildRes()
    const next = sinon.stub()

    await middleware.authenticate(req, res, next)

    const header = res.headers['WWW-Authenticate']
    expect(header).to.include('error_description="Missing bearer token"')
    // Directly exercise private helper path with dangerous chars by stubbing
    // invalid token branch.
    ;(middleware as any).unauthorized(res, 'bad \\\\ token "value"')
    expect(res.headers['WWW-Authenticate']).to.include(
      'error_description="bad \\\\\\\\ token \\"value\\""',
    )
  })

  it('returns 401 when client_id param is missing', async () => {
    const req: any = { headers: { authorization: 'Bearer rat-token' }, params: {} }
    const res = buildRes()
    const next = sinon.stub()

    await middleware.authenticate(req, res, next)

    expect(res.statusCode).to.equal(401)
    expect(res.body.error_description).to.equal('Missing client_id')
    expect(next.called).to.be.false
  })

  it('returns 401 when app is not found for client_id', async () => {
    sinon.stub(OAuthApp, 'findOne').resolves(null)
    const req: any = {
      headers: { authorization: 'Bearer rat-token' },
      params: { client_id: 'cid-1' },
    }
    const res = buildRes()
    const next = sinon.stub()

    await middleware.authenticate(req, res, next)

    expect(res.statusCode).to.equal(401)
    expect(res.body.error_description).to.equal('Invalid registration_access_token')
    expect(next.called).to.be.false
  })

  it('returns 401 when token hash does not match stored hash', async () => {
    const app = {
      _id: new Types.ObjectId(),
      clientId: 'cid-1',
      isDeleted: false,
      registeredVia: OAuthAppRegisteredVia.DCR,
      status: OAuthAppStatus.ACTIVE,
      registrationAccessTokenHash: 'aaaa',
    } as any
    sinon.stub(OAuthApp, 'findOne').resolves(app)
    mockTokenService.hashTokenPublic.returns('bbbb')
    const req: any = {
      headers: { authorization: 'Bearer rat-token' },
      params: { client_id: 'cid-1' },
    }
    const res = buildRes()
    const next = sinon.stub()

    await middleware.authenticate(req, res, next)

    expect(res.statusCode).to.equal(401)
    expect(res.body.error_description).to.equal('Invalid registration_access_token')
    expect(next.called).to.be.false
  })

  it('attaches oauthApp and calls next when registration token is valid', async () => {
    const app = {
      _id: new Types.ObjectId(),
      clientId: 'cid-1',
      isDeleted: false,
      registeredVia: OAuthAppRegisteredVia.DCR,
      status: OAuthAppStatus.ACTIVE,
      registrationAccessTokenHash: 'aaaa',
    } as any
    sinon.stub(OAuthApp, 'findOne').resolves(app)
    mockTokenService.hashTokenPublic.returns('aaaa')
    const req: any = {
      headers: { authorization: 'Bearer rat-token' },
      params: { client_id: 'cid-1' },
    }
    const res = buildRes()
    const next = sinon.stub()

    await middleware.authenticate(req, res, next)

    expect(req.oauthApp).to.equal(app)
    expect(next.calledOnce).to.be.true
    expect(res.status.called).to.be.false
  })
})
