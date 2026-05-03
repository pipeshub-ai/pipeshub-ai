import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import crypto from 'crypto'
import { OIDCProviderController } from '../../../../src/modules/oauth_provider/controller/oid.provider.controller'

describe('OIDCProviderController', () => {
  let controller: OIDCProviderController
  let mockOAuthTokenService: any
  let mockScopeValidatorService: any
  let mockAppConfig: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockOAuthTokenService = {
      getAlgorithm: sinon.stub().returns('HS256'),
      getPublicKey: sinon.stub().returns(undefined),
      getKeyId: sinon.stub().returns(undefined),
      verifyAccessToken: sinon.stub(),
    }
    mockScopeValidatorService = {
      getAllScopes: sinon.stub().returns([
        { name: 'org:read', description: 'Read org', category: 'Organization', requiresUserConsent: true },
      ]),
    }
    mockAppConfig = {
      oauthIssuer: 'http://localhost:3000',
      mcpScopes: ['org:read'],
    }
    controller = new OIDCProviderController(
      mockOAuthTokenService,
      mockScopeValidatorService,
      mockAppConfig,
    )
    mockRes = { json: sinon.stub(), status: sinon.stub().returnsThis(), setHeader: sinon.stub() }
    mockNext = sinon.stub()
  })

  afterEach(() => { sinon.restore() })

  describe('openidConfiguration', () => {
    it('should return valid OIDC configuration', async () => {
      await controller.openidConfiguration({} as any, mockRes, mockNext)
      const config = mockRes.json.firstCall.args[0]
      expect(config.issuer).to.equal('http://localhost:3000')
      expect(config.authorization_endpoint).to.include('/authorize')
      expect(config.token_endpoint).to.include('/token')
      expect(config.userinfo_endpoint).to.include('/userinfo')
      expect(config.response_types_supported).to.deep.equal(['code'])
      expect(config.grant_types_supported).to.include('authorization_code')
      expect(config.code_challenge_methods_supported).to.deep.equal(['S256', 'plain'])
    })
  })

  describe('oauthProtectedResource', () => {
    it('should return protected resource metadata', async () => {
      await controller.oauthProtectedResource({} as any, mockRes, mockNext)
      const meta = mockRes.json.firstCall.args[0]
      expect(meta.resource).to.include('/mcp')
      expect(meta.authorization_servers).to.deep.equal(['http://localhost:3000'])
      expect(meta.bearer_methods_supported).to.deep.equal(['header'])
    })
  })

  describe('jwks', () => {
    it('should return empty keys for HS256', async () => {
      await controller.jwks({} as any, mockRes, mockNext)
      const jwks = mockRes.json.firstCall.args[0]
      expect(jwks.keys).to.deep.equal([])
    })

    it('should return JWK for RS256 with valid public key', async () => {
      const { publicKey } = crypto.generateKeyPairSync('rsa', {
        modulusLength: 2048,
        publicKeyEncoding: { type: 'spki', format: 'pem' },
        privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
      })
      mockOAuthTokenService.getAlgorithm.returns('RS256')
      mockOAuthTokenService.getPublicKey.returns(publicKey)
      mockOAuthTokenService.getKeyId.returns('test-kid')

      await controller.jwks({} as any, mockRes, mockNext)
      const jwks = mockRes.json.firstCall.args[0]
      expect(jwks.keys).to.have.lengthOf(1)
      expect(jwks.keys[0].kty).to.equal('RSA')
      expect(jwks.keys[0].kid).to.equal('test-kid')
    })

    it('should return empty keys for RS256 without public key', async () => {
      mockOAuthTokenService.getAlgorithm.returns('RS256')
      mockOAuthTokenService.getPublicKey.returns(undefined)
      mockOAuthTokenService.getKeyId.returns(undefined)

      await controller.jwks({} as any, mockRes, mockNext)
      const jwks = mockRes.json.firstCall.args[0]
      expect(jwks.keys).to.deep.equal([])
    })
  })

  describe('userInfo', () => {
    it('should return 401 when user not found', async () => {
      const Users = require('../../../../src/modules/user_management/schema/users.schema').Users
      sinon.stub(Users, 'findById').resolves(null)

      const req = {
        oauth: {
          scopes: ['profile', 'email'],
          payload: { userId: 'user-1' },
        },
      } as any

      await controller.userInfo(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(401)).to.be.true
    })

    it('should return user info with profile and email scopes', async () => {
      const Users = require('../../../../src/modules/user_management/schema/users.schema').Users
      const mockUser = {
        _id: 'user-1',
        firstName: 'John',
        lastName: 'Doe',
        email: 'john@example.com',
        hasLoggedIn: true,
        updatedAt: new Date(),
      }
      sinon.stub(Users, 'findById').resolves(mockUser)

      const req = {
        oauth: {
          scopes: ['profile', 'email'],
          payload: { userId: 'user-1' },
        },
      } as any

      await controller.userInfo(req, mockRes, mockNext)
      const userInfo = mockRes.json.firstCall.args[0]
      expect(userInfo.name).to.equal('John Doe')
      expect(userInfo.email).to.equal('john@example.com')
      expect(userInfo.email_verified).to.be.true
    })
  })
})
