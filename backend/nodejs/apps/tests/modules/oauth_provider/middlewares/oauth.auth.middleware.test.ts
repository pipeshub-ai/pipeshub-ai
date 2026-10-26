import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  OAuthAuthMiddleware,
  buildWwwAuthenticateHeader,
  createOAuthMiddleware,
  scopeCheck,
} from '../../../../src/modules/oauth_provider/middlewares/oauth.auth.middleware'
import {
  InvalidTokenError,
  ExpiredTokenError,
} from '../../../../src/libs/errors/oauth.errors'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { ServiceAccountDelegation } from '../../../../src/modules/oauth_provider/schema/service-account-delegation.schema'

/** A Mongoose chain that ends in `value`. */
function mockQuery(value: unknown): any {
  return {
    select: sinon.stub().returnsThis(),
    lean: sinon.stub().returnsThis(),
    exec: sinon.stub().resolves(value),
  }
}

describe('OAuthAuthMiddleware', () => {
  let middleware: OAuthAuthMiddleware
  let mockLogger: any
  let mockOAuthTokenService: any
  let mockScopeValidatorService: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockLogger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    mockOAuthTokenService = {
      verifyAccessToken: sinon.stub(),
    }
    mockScopeValidatorService = {
      hasAllScopes: sinon.stub(),
      hasAnyScope: sinon.stub(),
    }
    middleware = new OAuthAuthMiddleware(mockLogger, mockOAuthTokenService, mockScopeValidatorService)
    mockRes = {
      json: sinon.stub(),
      status: sinon.stub().returnsThis(),
      setHeader: sinon.stub(),
    }
    mockNext = sinon.stub()
  })

  afterEach(() => { sinon.restore() })

  describe('authenticate', () => {
    it('should return 401 when no authorization header', async () => {
      const req = { headers: {} } as any
      await middleware.authenticate(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
      expect(mockRes.setHeader.calledOnce).to.be.true
    })

    it('should return 401 when header is not Bearer', async () => {
      const req = { headers: { authorization: 'Basic abc' } } as any
      await middleware.authenticate(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })

    it('should attach oauth data and call next for valid token', async () => {
      const payload = {
        client_id: 'cid',
        userId: 'user-1',
        orgId: 'org-1',
        scope: 'org:read org:write',
      }
      mockOAuthTokenService.verifyAccessToken.resolves(payload)
      const req = { headers: { authorization: 'Bearer valid-token' } } as any

      await middleware.authenticate(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      expect(req.oauth.clientId).to.equal('cid')
      expect(req.oauth.scopes).to.deep.equal(['org:read', 'org:write'])
    })

    it('should return 401 when the token carries no scopes', async () => {
      mockOAuthTokenService.verifyAccessToken.resolves({
        client_id: 'cid',
        userId: 'user-1',
        orgId: 'org-1',
        scope: '   ',
      })
      const req = { headers: { authorization: 'Bearer empty-scope' } } as any

      await middleware.authenticate(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(401)).to.be.true
      expect(mockRes.json.firstCall.args[0].error).to.equal('invalid_token')
      expect(mockRes.json.firstCall.args[0].error_description).to.equal(
        'Token carries no scopes',
      )
    })

    it('should return 401 for expired token', async () => {
      mockOAuthTokenService.verifyAccessToken.rejects(new ExpiredTokenError('expired'))
      const req = { headers: { authorization: 'Bearer expired-token' } } as any

      await middleware.authenticate(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
      const response = mockRes.json.firstCall.args[0]
      expect(response.error).to.equal('invalid_token')
    })

    it('should return 401 for invalid token', async () => {
      mockOAuthTokenService.verifyAccessToken.rejects(new InvalidTokenError('invalid'))
      const req = { headers: { authorization: 'Bearer bad-token' } } as any

      await middleware.authenticate(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })

    it('should return 401 for unknown errors', async () => {
      mockOAuthTokenService.verifyAccessToken.rejects(new Error('unknown'))
      const req = { headers: { authorization: 'Bearer token' } } as any

      await middleware.authenticate(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
      expect(mockLogger.error.calledOnce).to.be.true
    })
  })

  /**
   * The OIDC endpoints go through this middleware rather than AuthMiddleware,
   * so the rules behind a delegated token have to be re-checked here too.
   * Without it, `/oauth2/userinfo` keeps answering with the person's name and
   * email for the rest of the five minutes after the permission behind the
   * token was withdrawn.
   */
  describe('authenticate - delegated token', () => {
    const delegated = {
      client_id: 'cid',
      userId: 'the-person',
      orgId: 'org-1',
      scope: 'openid profile email',
      act: { sub: 'the-service-account' },
    }

    it('lets a delegated token through while the allow-list row is there', async () => {
      mockOAuthTokenService.verifyAccessToken.resolves(delegated)
      const users = sinon.stub(Users, 'findOne')
      users.onFirstCall().returns(mockQuery({ role: 'member', kind: 'human', isDisabled: false }))
      users.onSecondCall().returns(mockQuery({ isDisabled: false }))
      sinon.stub(ServiceAccountDelegation, 'findOne').returns(mockQuery({ _id: 'row-1' }))

      const req = { headers: { authorization: 'Bearer delegated' } } as any
      await middleware.authenticate(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(req.oauth.userId).to.equal('the-person')
    })

    it('refuses it once the allow-list row is gone', async () => {
      mockOAuthTokenService.verifyAccessToken.resolves(delegated)
      const users = sinon.stub(Users, 'findOne')
      users.onFirstCall().returns(mockQuery({ role: 'member', kind: 'human', isDisabled: false }))
      users.onSecondCall().returns(mockQuery({ isDisabled: false }))
      sinon.stub(ServiceAccountDelegation, 'findOne').returns(mockQuery(null))

      const req = { headers: { authorization: 'Bearer delegated' } } as any
      await middleware.authenticate(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(401)).to.be.true
      expect(mockRes.json.firstCall.args[0].error_description)
        .to.contain('may no longer act as this person')
    })

    it('refuses it once the person has been promoted to administrator', async () => {
      mockOAuthTokenService.verifyAccessToken.resolves(delegated)
      sinon.stub(Users, 'findOne').returns(
        mockQuery({ role: 'admin', kind: 'human', isDisabled: false }),
      )

      const req = { headers: { authorization: 'Bearer delegated' } } as any
      await middleware.authenticate(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(401)).to.be.true
      expect(mockRes.json.firstCall.args[0].error_description)
        .to.contain('now an administrator')
    })

    it('refuses it once the service account behind it is switched off', async () => {
      mockOAuthTokenService.verifyAccessToken.resolves(delegated)
      const users = sinon.stub(Users, 'findOne')
      users.onFirstCall().returns(mockQuery({ role: 'member', kind: 'human', isDisabled: false }))
      // The disable race the issuing path cannot close: the token exists, the
      // actor does not pass any more.
      users.onSecondCall().returns(mockQuery({ isDisabled: true }))

      const req = { headers: { authorization: 'Bearer delegated' } } as any
      await middleware.authenticate(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(401)).to.be.true
      expect(mockRes.json.firstCall.args[0].error_description)
        .to.contain('no longer active')
    })

    it('does not dress a failed read up as a withdrawn permission', async () => {
      mockOAuthTokenService.verifyAccessToken.resolves(delegated)
      const users = sinon.stub(Users, 'findOne')
      users.onFirstCall().returns(mockQuery({ role: 'member', kind: 'human', isDisabled: false }))
      users.onSecondCall().returns({
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().rejects(new Error('connection 7 to mongo timed out')),
      } as any)

      const req = { headers: { authorization: 'Bearer delegated' } } as any
      await middleware.authenticate(req, mockRes, mockNext)

      // Still refused, because failing open on an unreadable allow-list would
      // be worse than any wording.
      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(401)).to.be.true

      // But not with the driver's text, and not silently: an outage must not
      // read like an administrator having withdrawn a permission.
      const description = mockRes.json.firstCall.args[0].error_description
      expect(description).to.equal('Token validation failed')
      expect(description).to.not.contain('mongo')
      expect(mockLogger.error.called).to.be.true
    })

    it('leaves an ordinary token alone, with no extra reads', async () => {
      mockOAuthTokenService.verifyAccessToken.resolves({
        client_id: 'cid',
        userId: 'user-1',
        orgId: 'org-1',
        scope: 'openid',
      })
      const users = sinon.stub(Users, 'findOne')

      const req = { headers: { authorization: 'Bearer ordinary' } } as any
      await middleware.authenticate(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      // No `act` claim, so the delegation rules never run and this costs an
      // ordinary OAuth request nothing.
      expect(users.called).to.be.false
    })
  })

  describe('requireScopes', () => {
    it('should return 401 when not authenticated', async () => {
      const mw = middleware.requireScopes('org:read')
      const req = {} as any
      await mw(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })

    it('should return 403 for insufficient scopes', async () => {
      mockScopeValidatorService.hasAllScopes.returns(false)
      const mw = middleware.requireScopes('org:read', 'org:write')
      const req = { oauth: { scopes: ['org:read'] } } as any
      await mw(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(403)).to.be.true
      const response = mockRes.json.firstCall.args[0]
      expect(response.error).to.equal('insufficient_scope')
    })

    it('should call next when all scopes present', async () => {
      mockScopeValidatorService.hasAllScopes.returns(true)
      const mw = middleware.requireScopes('org:read')
      const req = { oauth: { scopes: ['org:read'] } } as any
      await mw(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('requireAnyScope', () => {
    it('should return 401 when not authenticated', async () => {
      const mw = middleware.requireAnyScope('org:read')
      const req = {} as any
      await mw(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })

    it('should return 403 when no scope matches', async () => {
      mockScopeValidatorService.hasAnyScope.returns(false)
      const mw = middleware.requireAnyScope('org:admin')
      const req = { oauth: { scopes: ['org:read'] } } as any
      await mw(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(403)).to.be.true
    })

    it('should call next when any scope matches', async () => {
      mockScopeValidatorService.hasAnyScope.returns(true)
      const mw = middleware.requireAnyScope('org:read', 'org:write')
      const req = { oauth: { scopes: ['org:read'] } } as any
      await mw(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })
})

describe('buildWwwAuthenticateHeader', () => {
  it('should build basic header with realm', () => {
    const header = buildWwwAuthenticateHeader()
    expect(header).to.equal('Bearer realm="oauth"')
  })

  it('should include error when provided', () => {
    const header = buildWwwAuthenticateHeader('invalid_token')
    expect(header).to.include('error="invalid_token"')
  })

  it('should include error description when provided', () => {
    const header = buildWwwAuthenticateHeader('invalid_token', 'Token expired')
    expect(header).to.include('error_description="Token expired"')
  })

  it('should escape quotes in description', () => {
    const header = buildWwwAuthenticateHeader('invalid_token', 'Token "bad"')
    expect(header).to.include('error_description="Token \\"bad\\""')
  })

  it('should include scope when provided', () => {
    const header = buildWwwAuthenticateHeader('insufficient_scope', 'Need scopes', 'org:read')
    expect(header).to.include('scope="org:read"')
  })

  it('should use custom realm', () => {
    const header = buildWwwAuthenticateHeader(undefined, undefined, undefined, 'custom')
    expect(header).to.include('realm="custom"')
  })
})

describe('createOAuthMiddleware', () => {
  it('should create middleware instance', () => {
    const mockLogger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() } as any
    const result = createOAuthMiddleware({} as any, {} as any, mockLogger)
    expect(result).to.be.instanceOf(OAuthAuthMiddleware)
  })
})

describe('scopeCheck', () => {
  let mockOAuthTokenService: any
  let mockScopeValidatorService: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockOAuthTokenService = { verifyAccessToken: sinon.stub() }
    mockScopeValidatorService = { hasAllScopes: sinon.stub() }
    mockRes = { json: sinon.stub(), status: sinon.stub().returnsThis() }
    mockNext = sinon.stub()
  })

  afterEach(() => { sinon.restore() })

  it('should call next when no Bearer token', async () => {
    const mw = scopeCheck(['org:read'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: {} } as any
    await mw(req, mockRes, mockNext)
    expect(mockNext.calledOnce).to.be.true
  })

  it('should attach oauth and call next for valid token with scopes', async () => {
    const payload = {
      client_id: 'cid', userId: 'u1', orgId: 'o1', scope: 'org:read',
    }
    mockOAuthTokenService.verifyAccessToken.resolves(payload)
    mockScopeValidatorService.hasAllScopes.returns(true)

    const mw = scopeCheck(['org:read'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: { authorization: 'Bearer valid-token' } } as any
    await mw(req, mockRes, mockNext)
    expect(mockNext.calledOnce).to.be.true
    expect(req.oauth).to.exist
  })

  it('should return 403 for insufficient scopes', async () => {
    const payload = { client_id: 'cid', userId: 'u1', orgId: 'o1', scope: 'org:read' }
    mockOAuthTokenService.verifyAccessToken.resolves(payload)
    mockScopeValidatorService.hasAllScopes.returns(false)

    const mw = scopeCheck(['org:admin'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: { authorization: 'Bearer token' } } as any
    await mw(req, mockRes, mockNext)
    expect(mockRes.status.calledWith(403)).to.be.true
  })

  it('should return 401 when the token carries no scopes', async () => {
    mockOAuthTokenService.verifyAccessToken.resolves({
      client_id: 'cid', userId: 'u1', orgId: 'o1', scope: '',
    })
    const mw = scopeCheck(['org:read'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: { authorization: 'Bearer token' } } as any
    await mw(req, mockRes, mockNext)
    expect(mockNext.called).to.be.false
    expect(mockRes.status.calledWith(401)).to.be.true
    expect(mockRes.json.firstCall.args[0].error).to.equal('invalid_token')
  })

  it('should return 401 when verification throws for an empty scope', async () => {
    mockOAuthTokenService.verifyAccessToken.rejects(
      new InvalidTokenError('Token carries no scopes'),
    )
    const mw = scopeCheck(['org:read'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: { authorization: 'Bearer token' } } as any
    await mw(req, mockRes, mockNext)
    expect(mockNext.called).to.be.false
    expect(mockRes.status.calledWith(401)).to.be.true
    expect(mockRes.json.firstCall.args[0]).to.deep.equal({
      error: 'invalid_token',
      error_description: 'Token carries no scopes',
    })
  })

  it('should fall through when a different invalid-token error is thrown', async () => {
    mockOAuthTokenService.verifyAccessToken.rejects(
      new InvalidTokenError('Token has been revoked'),
    )
    const mw = scopeCheck(['org:read'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: { authorization: 'Bearer token' } } as any
    await mw(req, mockRes, mockNext)
    expect(mockNext.calledOnce).to.be.true
    expect(mockRes.status.called).to.be.false
  })

  it('should fall through to next middleware on token verification error', async () => {
    mockOAuthTokenService.verifyAccessToken.rejects(new Error('invalid'))
    const mw = scopeCheck(['org:read'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: { authorization: 'Bearer bad-token' } } as any
    await mw(req, mockRes, mockNext)
    expect(mockNext.calledOnce).to.be.true
  })
})
