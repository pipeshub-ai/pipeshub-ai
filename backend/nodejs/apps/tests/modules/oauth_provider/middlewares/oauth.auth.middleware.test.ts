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

  it('should fall through to next middleware on token verification error', async () => {
    mockOAuthTokenService.verifyAccessToken.rejects(new Error('invalid'))
    const mw = scopeCheck(['org:read'], mockOAuthTokenService, mockScopeValidatorService)
    const req = { headers: { authorization: 'Bearer bad-token' } } as any
    await mw(req, mockRes, mockNext)
    expect(mockNext.calledOnce).to.be.true
  })
})
