import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OAuthProviderController } from '../../../../src/modules/oauth_provider/controller/oauth.provider.controller'
import {
  InvalidClientError,
  InvalidGrantError,
  UnsupportedGrantTypeError,
  InvalidScopeError,
  AccessDeniedError,
} from '../../../../src/libs/errors/oauth.errors'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'

describe('OAuthProviderController', () => {
  let controller: OAuthProviderController
  let mockLogger: any
  let mockOAuthAppService: any
  let mockOAuthTokenService: any
  let mockAuthCodeService: any
  let mockScopeValidatorService: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockLogger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    mockOAuthAppService = {
      getAppByClientId: sinon.stub(),
      validateRedirectUriForApp: sinon.stub(),
      verifyClientCredentials: sinon.stub(),
      isGrantTypeAllowed: sinon.stub(),
    }
    mockOAuthTokenService = {
      generateTokens: sinon.stub(),
      revokeToken: sinon.stub(),
      introspectToken: sinon.stub(),
      refreshTokens: sinon.stub(),
    }
    mockAuthCodeService = {
      generateCode: sinon.stub(),
      exchangeCode: sinon.stub(),
    }
    mockScopeValidatorService = {
      parseScopes: sinon.stub().returns(['org:read']),
      validateScopesForApp: sinon.stub(),
      getScopeDefinitions: sinon.stub().returns([{ name: 'org:read', description: 'Read org', category: 'Organization' }]),
    }
    controller = new OAuthProviderController(
      mockLogger,
      mockOAuthAppService,
      mockOAuthTokenService,
      mockAuthCodeService,
      mockScopeValidatorService,
    )
    mockRes = {
      json: sinon.stub(),
      status: sinon.stub().returnsThis(),
      setHeader: sinon.stub(),
      send: sinon.stub(),
    }
    mockNext = sinon.stub()
  })

  afterEach(() => { sinon.restore() })

  describe('authorize', () => {
    it('should return 400 for invalid client_id', async () => {
      mockOAuthAppService.getAppByClientId.rejects(new InvalidClientError('bad client'))
      const req = {
        query: { client_id: 'bad', redirect_uri: 'https://example.com/cb', scope: 'org:read' },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(400)).to.be.true
    })

    it('should return consent data for valid request', async () => {
      const mockApp = {
        name: 'App', description: 'Desc', allowedScopes: ['org:read'],
        isConfidential: true, logoUrl: null, homepageUrl: null, privacyPolicyUrl: null,
        createdBy: { toString: () => 'u1' },
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      const req = {
        query: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1',
        },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com', fullName: 'Test User' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
      const response = mockRes.json.firstCall.args[0]
      expect(response.requiresConsent).to.be.true
    })

    it('should return access_denied redirect when user is not the app creator', async () => {
      const mockApp = {
        name: 'App', description: 'Desc', allowedScopes: ['org:read'],
        isConfidential: true, logoUrl: null, homepageUrl: null, privacyPolicyUrl: null,
        createdBy: { toString: () => 'creator-id' },
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      const req = {
        query: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1',
        },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com', fullName: 'Test User' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.redirectUrl).to.include('error=access_denied')
      expect(response.redirectUrl).to.include('state=state1')
    })
  })

  describe('authorizeConsent', () => {
    it('should return redirect URL with code when consent granted', async () => {
      mockOAuthAppService.getAppByClientId.resolves({
        allowedScopes: ['org:read'],
        createdBy: { toString: () => 'u1' },
      })
      mockAuthCodeService.generateCode.resolves('auth-code-123')
      const req = {
        body: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1', consent: 'granted',
        },
        user: { userId: 'u1', orgId: 'o1' },
      } as any

      await controller.authorizeConsent(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.redirectUrl).to.include('code=auth-code-123')
    })

    it('should return error redirect when consent denied', async () => {
      mockOAuthAppService.getAppByClientId.resolves({
        allowedScopes: ['org:read'],
        createdBy: { toString: () => 'u1' },
      })
      const req = {
        body: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1', consent: 'denied',
        },
        user: { userId: 'u1', orgId: 'o1' },
      } as any

      await controller.authorizeConsent(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.redirectUrl).to.include('access_denied')
    })
  })

  describe('token', () => {
    it('should return 400 for unsupported grant type', async () => {
      const req = {
        body: { grant_type: 'password', client_id: 'cid' },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(400)).to.be.true
    })

    it('should return error for missing client_id', async () => {
      const req = {
        body: { grant_type: 'authorization_code' },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.called).to.be.true
    })

    it('should parse Basic auth header', async () => {
      const basicAuth = Buffer.from('client-id:client-secret').toString('base64')
      mockOAuthAppService.getAppByClientId.resolves({
        isConfidential: false,
        allowedGrantTypes: ['authorization_code'],
      })
      mockOAuthAppService.isGrantTypeAllowed.returns(true)
      mockAuthCodeService.exchangeCode.resolves({
        userId: 'u1', orgId: 'o1', scopes: ['org:read'],
      })
      mockOAuthTokenService.generateTokens.resolves({
        accessToken: 'at', tokenType: 'Bearer', expiresIn: 3600, scope: 'org:read',
      })

      // Stub mongoose models to prevent DB access
      const chainable = { select: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves(null) }
      sinon.stub(Users, 'findOne').returns(chainable as any)
      sinon.stub(Org, 'findOne').returns(chainable as any)

      const req = {
        body: {
          grant_type: 'authorization_code',
          code: 'auth-code',
          redirect_uri: 'https://example.com/cb',
        },
        headers: { authorization: `Basic ${basicAuth}` },
      } as any

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.setHeader.calledWith('Cache-Control', 'no-store')).to.be.true
    })

    it('should set cache control headers on success', async () => {
      const req = {
        body: {
          grant_type: 'client_credentials',
          client_id: 'cid',
          client_secret: 'secret',
        },
        headers: {},
      } as any

      const mockApp = {
        clientId: 'cid',
        orgId: { toString: () => 'org-1' },
        allowedScopes: ['org:read'],
        isConfidential: true,
        createdBy: { toString: () => 'owner-1' },
      }
      mockOAuthAppService.verifyClientCredentials.resolves(mockApp)
      mockOAuthAppService.isGrantTypeAllowed.returns(true)
      mockOAuthTokenService.generateTokens.resolves({
        accessToken: 'at', tokenType: 'Bearer', expiresIn: 3600, scope: 'org:read',
      })

      // Stub mongoose models to prevent DB access
      const chainable = { select: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves(null) }
      sinon.stub(Users, 'findOne').returns(chainable as any)
      sinon.stub(Org, 'findOne').returns(chainable as any)

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.setHeader.calledWith('Cache-Control', 'no-store')).to.be.true
      expect(mockRes.setHeader.calledWith('Pragma', 'no-cache')).to.be.true
    })
  })

  describe('revoke', () => {
    it('should return 200 even for invalid token', async () => {
      mockOAuthAppService.verifyClientCredentials.resolves({})
      mockOAuthTokenService.revokeToken.resolves(false)
      const req = {
        body: { token: 'tok', client_id: 'cid', client_secret: 'secret' },
      } as any

      await controller.revoke(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(200)).to.be.true
    })

    it('should return 401 for invalid client credentials', async () => {
      mockOAuthAppService.verifyClientCredentials.rejects(new InvalidClientError('bad'))
      const req = {
        body: { token: 'tok', client_id: 'cid', client_secret: 'wrong' },
      } as any

      await controller.revoke(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })
  })

  describe('introspect', () => {
    it('should return introspection result', async () => {
      mockOAuthAppService.verifyClientCredentials.resolves({})
      mockOAuthTokenService.introspectToken.resolves({ active: true })
      const req = {
        body: { token: 'tok', client_id: 'cid', client_secret: 'secret' },
      } as any

      await controller.introspect(req, mockRes, mockNext)
      expect(mockRes.json.calledWith({ active: true })).to.be.true
    })

    it('should return inactive on error', async () => {
      mockOAuthAppService.verifyClientCredentials.resolves({})
      mockOAuthTokenService.introspectToken.rejects(new Error('fail'))
      const req = {
        body: { token: 'tok', client_id: 'cid', client_secret: 'secret' },
      } as any

      await controller.introspect(req, mockRes, mockNext)
      expect(mockRes.json.calledWith({ active: false })).to.be.true
    })
  })

  describe('buildErrorResponse (private)', () => {
    it('should map different error types to correct codes', () => {
      const buildErr = (controller as any).buildErrorResponse.bind(controller)

      expect(buildErr(new InvalidGrantError('test')).error).to.equal('invalid_grant')
      expect(buildErr(new InvalidClientError('test')).error).to.equal('invalid_client')
      expect(buildErr(new UnsupportedGrantTypeError('test')).error).to.equal('unsupported_grant_type')
      expect(buildErr(new InvalidScopeError('test')).error).to.equal('invalid_scope')
      expect(buildErr(new AccessDeniedError('test')).error).to.equal('access_denied')
      expect(buildErr(new Error('test')).error).to.equal('server_error')
    })

    it('should include state when provided', () => {
      const buildErr = (controller as any).buildErrorResponse.bind(controller)
      const result = buildErr(new Error('test'), 'state123')
      expect(result.state).to.equal('state123')
    })
  })

  describe('getErrorStatusCode (private)', () => {
    it('should return correct status codes', () => {
      const getStatus = (controller as any).getErrorStatusCode.bind(controller)
      expect(getStatus(new InvalidClientError('test'))).to.equal(401)
      expect(getStatus(new AccessDeniedError('test'))).to.equal(403)
      expect(getStatus(new Error('test'))).to.equal(400)
    })
  })

  // -----------------------------------------------------------------------
  // authorize – additional branches
  // -----------------------------------------------------------------------
  describe('authorize (extended)', () => {
    it('should return 400 for invalid redirect URI', async () => {
      const { InvalidRedirectUriError } = require('../../../../src/libs/errors/oauth.errors')
      mockOAuthAppService.getAppByClientId.resolves({ name: 'App' })
      mockOAuthAppService.validateRedirectUriForApp.throws(new InvalidRedirectUriError('bad redirect'))

      const req = {
        query: { client_id: 'cid', redirect_uri: 'bad-uri', scope: 'org:read' },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(400)).to.be.true
      const response = mockRes.json.firstCall.args[0]
      expect(response.error).to.equal('invalid_request')
    })

    it('should redirect with PKCE error for public client without code_challenge', async () => {
      const mockApp = {
        name: 'App', description: 'Desc', allowedScopes: ['org:read'],
        isConfidential: false,
        createdBy: { toString: () => 'u1' },
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      const req = {
        query: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1',
        },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.redirectUrl).to.include('PKCE')
      expect(response.redirectUrl).to.include('state=state1')
    })

    it('should redirect with PKCE error without state', async () => {
      const mockApp = {
        name: 'App', description: 'Desc', allowedScopes: ['org:read'],
        isConfidential: false,
        createdBy: { toString: () => 'u1' },
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      const req = {
        query: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read',
        },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.redirectUrl).to.include('PKCE')
      expect(response.redirectUrl).to.not.include('state=')
    })

    it('should redirect error when scope validation fails', async () => {
      const { InvalidScopeError: ISE } = require('../../../../src/libs/errors/oauth.errors')
      const mockApp = {
        name: 'App', description: 'Desc', allowedScopes: ['org:read'],
        isConfidential: true,
        createdBy: { toString: () => 'u1' },
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      mockScopeValidatorService.parseScopes.throws(new ISE('bad scope'))

      const req = {
        query: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'invalid:scope', state: 'state1',
        },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.redirectUrl).to.include('invalid_scope')
    })

    it('should include code_challenge params in consent response', async () => {
      const mockApp = {
        name: 'App', description: 'Desc', allowedScopes: ['org:read'],
        isConfidential: true, logoUrl: null, homepageUrl: null, privacyPolicyUrl: null,
        createdBy: { toString: () => 'u1' },
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      const req = {
        query: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1',
          code_challenge: 'challenge', code_challenge_method: 'S256',
        },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com', fullName: 'Test' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.codeChallenge).to.equal('challenge')
      expect(response.codeChallengeMethod).to.equal('S256')
    })

    it('should call next for unknown errors before redirect_uri validation', async () => {
      mockOAuthAppService.getAppByClientId.rejects(new Error('DB error'))

      const req = {
        query: { client_id: 'cid', redirect_uri: 'https://example.com/cb', scope: 'org:read' },
        user: { userId: 'u1', orgId: 'o1', email: 'u@e.com' },
      } as any

      await controller.authorize(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // authorizeConsent – additional branches
  // -----------------------------------------------------------------------
  describe('authorizeConsent (extended)', () => {
    it('should call next on error', async () => {
      mockOAuthAppService.getAppByClientId.rejects(new Error('DB error'))
      const req = {
        body: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1', consent: 'granted',
        },
        user: { userId: 'u1', orgId: 'o1' },
      } as any

      await controller.authorizeConsent(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should include PKCE params in code generation', async () => {
      mockOAuthAppService.getAppByClientId.resolves({
        allowedScopes: ['org:read'],
        createdBy: { toString: () => 'u1' },
      })
      mockAuthCodeService.generateCode.resolves('code-1')
      const req = {
        body: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1', consent: 'granted',
          code_challenge: 'ch', code_challenge_method: 'S256',
        },
        user: { userId: 'u1', orgId: 'o1' },
      } as any

      await controller.authorizeConsent(req, mockRes, mockNext)
      expect(mockAuthCodeService.generateCode.calledOnce).to.be.true
      const args = mockAuthCodeService.generateCode.firstCall.args
      expect(args[5]).to.equal('ch')
      expect(args[6]).to.equal('S256')
    })

    it('should return access_denied when user is not the app creator', async () => {
      mockOAuthAppService.getAppByClientId.resolves({
        allowedScopes: ['org:read'],
        createdBy: { toString: () => 'creator-id' },
      })
      const req = {
        body: {
          client_id: 'cid', redirect_uri: 'https://example.com/cb',
          scope: 'org:read', state: 'state1', consent: 'granted',
        },
        user: { userId: 'u1', orgId: 'o1' },
      } as any

      await controller.authorizeConsent(req, mockRes, mockNext)
      const response = mockRes.json.firstCall.args[0]
      expect(response.redirectUrl).to.include('error=access_denied')
      expect(response.redirectUrl).to.include('state=state1')
    })
  })

  // -----------------------------------------------------------------------
  // token – refresh_token grant
  // -----------------------------------------------------------------------
  describe('token - refresh_token grant', () => {
    it('should return error when refresh_token is missing', async () => {
      const req = {
        body: { grant_type: 'refresh_token', client_id: 'cid' },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(400)).to.be.true
      const response = mockRes.json.firstCall.args[0]
      expect(response.error).to.equal('invalid_grant')
    })

    it('should handle refresh token successfully', async () => {
      const mockApp = {
        isConfidential: false,
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      mockOAuthAppService.isGrantTypeAllowed.returns(true)
      mockOAuthTokenService.refreshTokens.resolves({
        accessToken: 'new-at', tokenType: 'Bearer', expiresIn: 3600,
        refreshToken: 'new-rt', scope: 'org:read',
      })

      const req = {
        body: {
          grant_type: 'refresh_token',
          client_id: 'cid',
          refresh_token: 'old-rt',
        },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
      const response = mockRes.json.firstCall.args[0]
      expect(response.access_token).to.equal('new-at')
      expect(response.refresh_token).to.equal('new-rt')
    })

    it('should require client_secret for confidential clients on refresh', async () => {
      const mockApp = {
        isConfidential: true,
      }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      mockOAuthAppService.isGrantTypeAllowed.returns(true)

      const req = {
        body: {
          grant_type: 'refresh_token',
          client_id: 'cid',
          refresh_token: 'rt',
        },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })

    it('should pass requested scopes to refreshTokens', async () => {
      const mockApp = { isConfidential: false }
      mockOAuthAppService.getAppByClientId.resolves(mockApp)
      mockOAuthAppService.isGrantTypeAllowed.returns(true)
      mockScopeValidatorService.parseScopes.returns(['org:read'])
      mockOAuthTokenService.refreshTokens.resolves({
        accessToken: 'at', tokenType: 'Bearer', expiresIn: 3600, scope: 'org:read',
      })

      const req = {
        body: {
          grant_type: 'refresh_token',
          client_id: 'cid',
          refresh_token: 'rt',
          scope: 'org:read',
        },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      const args = mockOAuthTokenService.refreshTokens.firstCall.args
      expect(args[2]).to.deep.equal(['org:read'])
    })
  })

  // -----------------------------------------------------------------------
  // token – authorization_code grant additional branches
  // -----------------------------------------------------------------------
  describe('token - authorization_code grant (extended)', () => {
    it('should return error when code is missing', async () => {
      const req = {
        body: { grant_type: 'authorization_code', client_id: 'cid', redirect_uri: 'https://ex.com/cb' },
        headers: {},
      } as any

      mockOAuthAppService.getAppByClientId.resolves({ isConfidential: false })
      mockOAuthAppService.isGrantTypeAllowed.returns(true)

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(400)).to.be.true
    })

    it('should return error when redirect_uri is missing', async () => {
      const req = {
        body: { grant_type: 'authorization_code', client_id: 'cid', code: 'code' },
        headers: {},
      } as any

      mockOAuthAppService.getAppByClientId.resolves({ isConfidential: false })
      mockOAuthAppService.isGrantTypeAllowed.returns(true)

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(400)).to.be.true
    })

    it('should return error when grant type not allowed', async () => {
      const req = {
        body: { grant_type: 'authorization_code', client_id: 'cid', code: 'code', redirect_uri: 'https://ex.com/cb' },
        headers: {},
      } as any

      mockOAuthAppService.getAppByClientId.resolves({ isConfidential: false })
      mockOAuthAppService.isGrantTypeAllowed.returns(false)

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(400)).to.be.true
    })

    it('should require client_secret for confidential clients', async () => {
      const req = {
        body: { grant_type: 'authorization_code', client_id: 'cid', code: 'code', redirect_uri: 'https://ex.com/cb' },
        headers: {},
      } as any

      mockOAuthAppService.getAppByClientId.resolves({ isConfidential: true })
      mockOAuthAppService.isGrantTypeAllowed.returns(true)

      await controller.token(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // token – client_credentials grant additional branches
  // -----------------------------------------------------------------------
  describe('token - client_credentials grant (extended)', () => {
    it('should filter user-specific scopes', async () => {
      const mockApp = {
        clientId: 'cid',
        orgId: { toString: () => 'org-1' },
        allowedScopes: ['org:read', 'openid', 'profile', 'email', 'offline_access'],
        isConfidential: true,
        createdBy: { toString: () => 'owner-1' },
      }
      mockOAuthAppService.verifyClientCredentials.resolves(mockApp)
      mockOAuthAppService.isGrantTypeAllowed.returns(true)
      mockOAuthTokenService.generateTokens.resolves({
        accessToken: 'at', tokenType: 'Bearer', expiresIn: 3600, scope: 'org:read',
      })

      const chainable = { select: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves(null) }
      sinon.stub(Users, 'findOne').returns(chainable as any)
      sinon.stub(Org, 'findOne').returns(chainable as any)

      const req = {
        body: { grant_type: 'client_credentials', client_id: 'cid', client_secret: 'secret' },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      // generateTokens should be called with filtered scopes (only org:read)
      const args = mockOAuthTokenService.generateTokens.firstCall.args
      expect(args[3]).to.deep.equal(['org:read'])
    })

    it('should use requested scopes when provided', async () => {
      const mockApp = {
        clientId: 'cid',
        orgId: { toString: () => 'org-1' },
        allowedScopes: ['org:read', 'document:read'],
        isConfidential: true,
        createdBy: { toString: () => 'owner-1' },
      }
      mockOAuthAppService.verifyClientCredentials.resolves(mockApp)
      mockOAuthAppService.isGrantTypeAllowed.returns(true)
      mockScopeValidatorService.parseScopes.returns(['org:read'])
      mockOAuthTokenService.generateTokens.resolves({
        accessToken: 'at', tokenType: 'Bearer', expiresIn: 3600, scope: 'org:read',
      })

      const chainable = { select: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves(null) }
      sinon.stub(Users, 'findOne').returns(chainable as any)
      sinon.stub(Org, 'findOne').returns(chainable as any)

      const req = {
        body: { grant_type: 'client_credentials', client_id: 'cid', client_secret: 'secret', scope: 'org:read' },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      expect(mockScopeValidatorService.validateScopesForApp.calledOnce).to.be.true
    })

    it('should look up user and org for fullName and accountType', async () => {
      const mockApp = {
        clientId: 'cid',
        orgId: { toString: () => 'org-1' },
        allowedScopes: ['org:read'],
        isConfidential: true,
        createdBy: 'creator-id',
      }
      mockOAuthAppService.verifyClientCredentials.resolves(mockApp)
      mockOAuthAppService.isGrantTypeAllowed.returns(true)
      mockOAuthTokenService.generateTokens.resolves({
        accessToken: 'at', tokenType: 'Bearer', expiresIn: 3600, scope: 'org:read',
      })

      const userChainable = { select: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves({ fullName: 'Creator Name' }) }
      const orgChainable = { select: sinon.stub().returnsThis(), lean: sinon.stub().returnsThis(), exec: sinon.stub().resolves({ accountType: 'premium' }) }
      sinon.stub(Users, 'findOne').returns(userChainable as any)
      sinon.stub(Org, 'findOne').returns(orgChainable as any)

      const req = {
        body: { grant_type: 'client_credentials', client_id: 'cid', client_secret: 'secret' },
        headers: {},
      } as any

      await controller.token(req, mockRes, mockNext)
      const args = mockOAuthTokenService.generateTokens.firstCall.args
      expect(args[5]).to.equal('Creator Name')
      expect(args[6]).to.equal('premium')
    })
  })

  // -----------------------------------------------------------------------
  // revoke – additional branches
  // -----------------------------------------------------------------------
  describe('revoke (extended)', () => {
    it('should return 200 on non-client errors', async () => {
      mockOAuthAppService.verifyClientCredentials.resolves({})
      mockOAuthTokenService.revokeToken.rejects(new Error('token not found'))
      const req = {
        body: { token: 'tok', client_id: 'cid', client_secret: 'secret' },
      } as any

      await controller.revoke(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(200)).to.be.true
    })

    it('should pass token_type_hint to revokeToken', async () => {
      mockOAuthAppService.verifyClientCredentials.resolves({})
      mockOAuthTokenService.revokeToken.resolves()
      const req = {
        body: { token: 'tok', client_id: 'cid', client_secret: 'secret', token_type_hint: 'refresh_token' },
      } as any

      await controller.revoke(req, mockRes, mockNext)
      expect(mockOAuthTokenService.revokeToken.firstCall.args[2]).to.equal('refresh_token')
    })
  })

  // -----------------------------------------------------------------------
  // introspect – additional branches
  // -----------------------------------------------------------------------
  describe('introspect (extended)', () => {
    it('should return 401 for invalid client on introspection', async () => {
      mockOAuthAppService.verifyClientCredentials.rejects(new InvalidClientError('bad client'))
      const req = {
        body: { token: 'tok', client_id: 'bad', client_secret: 'secret' },
      } as any

      await controller.introspect(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })
  })
})
