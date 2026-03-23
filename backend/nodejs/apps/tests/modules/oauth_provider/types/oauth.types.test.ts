import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('oauth_provider/types/oauth.types', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/oauth_provider/types/oauth.types')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/oauth_provider/types/oauth.types')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('OAuthTokenPayload interface', () => {
    it('should allow creating conforming objects', () => {
      const payload: import('../../../../src/modules/oauth_provider/types/oauth.types').OAuthTokenPayload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: 'https://auth.example.com',
        exp: 1700000000,
        iat: 1699990000,
        jti: 'jti-123',
        scope: 'read write',
        client_id: 'client-1',
        tokenType: 'oauth',
      }
      expect(payload.userId).to.equal('user-1')
      expect(payload.tokenType).to.equal('oauth')
    })

    it('should allow optional fields', () => {
      const payload: import('../../../../src/modules/oauth_provider/types/oauth.types').OAuthTokenPayload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: 'https://auth.example.com',
        exp: 1700000000,
        iat: 1699990000,
        jti: 'jti-123',
        scope: 'read',
        client_id: 'client-1',
        tokenType: 'oauth',
        isRefreshToken: true,
        fullName: 'Test User',
        accountType: 'admin',
        createdBy: 'system',
      }
      expect(payload.isRefreshToken).to.be.true
      expect(payload.fullName).to.equal('Test User')
    })
  })

  describe('GeneratedTokens interface', () => {
    it('should allow creating conforming objects', () => {
      const tokens: import('../../../../src/modules/oauth_provider/types/oauth.types').GeneratedTokens = {
        accessToken: 'access-token-value',
        tokenType: 'Bearer',
        expiresIn: 3600,
        scope: 'read write',
      }
      expect(tokens.accessToken).to.equal('access-token-value')
      expect(tokens.tokenType).to.equal('Bearer')
      expect(tokens.expiresIn).to.equal(3600)
    })
  })

  describe('TokenResponse interface', () => {
    it('should allow creating conforming objects', () => {
      const response: import('../../../../src/modules/oauth_provider/types/oauth.types').TokenResponse = {
        access_token: 'token-value',
        token_type: 'Bearer',
        expires_in: 3600,
        scope: 'read',
      }
      expect(response.access_token).to.equal('token-value')
    })
  })

  describe('AuthorizeRequest interface', () => {
    it('should allow creating conforming objects', () => {
      const request: import('../../../../src/modules/oauth_provider/types/oauth.types').AuthorizeRequest = {
        response_type: 'code',
        client_id: 'client-1',
        redirect_uri: 'https://example.com/callback',
        scope: 'read',
        state: 'random-state',
      }
      expect(request.response_type).to.equal('code')
    })

    it('should allow PKCE fields', () => {
      const request: import('../../../../src/modules/oauth_provider/types/oauth.types').AuthorizeRequest = {
        response_type: 'code',
        client_id: 'client-1',
        redirect_uri: 'https://example.com/callback',
        scope: 'read',
        state: 'random-state',
        code_challenge: 'challenge-value',
        code_challenge_method: 'S256',
      }
      expect(request.code_challenge_method).to.equal('S256')
    })
  })

  describe('TokenRequest interface', () => {
    it('should allow authorization_code grant', () => {
      const request: import('../../../../src/modules/oauth_provider/types/oauth.types').TokenRequest = {
        grant_type: 'authorization_code',
        client_id: 'client-1',
        code: 'auth-code',
        redirect_uri: 'https://example.com/callback',
      }
      expect(request.grant_type).to.equal('authorization_code')
    })

    it('should allow refresh_token grant', () => {
      const request: import('../../../../src/modules/oauth_provider/types/oauth.types').TokenRequest = {
        grant_type: 'refresh_token',
        client_id: 'client-1',
        refresh_token: 'refresh-token-value',
      }
      expect(request.grant_type).to.equal('refresh_token')
    })
  })

  describe('RevokeRequest interface', () => {
    it('should allow creating conforming objects', () => {
      const request: import('../../../../src/modules/oauth_provider/types/oauth.types').RevokeRequest = {
        token: 'token-to-revoke',
        client_id: 'client-1',
        token_type_hint: 'access_token',
      }
      expect(request.token).to.equal('token-to-revoke')
    })
  })

  describe('IntrospectResponse interface', () => {
    it('should allow creating conforming objects', () => {
      const response: import('../../../../src/modules/oauth_provider/types/oauth.types').IntrospectResponse = {
        active: true,
        scope: 'read write',
        client_id: 'client-1',
        token_type: 'Bearer',
      }
      expect(response.active).to.be.true
    })
  })

  describe('CreateOAuthAppRequest interface', () => {
    it('should allow creating conforming objects', () => {
      const request: import('../../../../src/modules/oauth_provider/types/oauth.types').CreateOAuthAppRequest = {
        name: 'Test App',
        allowedScopes: ['read', 'write'],
      }
      expect(request.name).to.equal('Test App')
      expect(request.allowedScopes).to.have.lengthOf(2)
    })
  })

  describe('OpenIDConfiguration interface', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../src/modules/oauth_provider/types/oauth.types').OpenIDConfiguration = {
        issuer: 'https://auth.example.com',
        authorization_endpoint: 'https://auth.example.com/authorize',
        token_endpoint: 'https://auth.example.com/token',
        userinfo_endpoint: 'https://auth.example.com/userinfo',
        revocation_endpoint: 'https://auth.example.com/revoke',
        introspection_endpoint: 'https://auth.example.com/introspect',
        jwks_uri: 'https://auth.example.com/.well-known/jwks.json',
        scopes_supported: ['openid', 'profile'],
        response_types_supported: ['code'],
        grant_types_supported: ['authorization_code'],
        token_endpoint_auth_methods_supported: ['client_secret_basic'],
        subject_types_supported: ['public'],
        id_token_signing_alg_values_supported: ['RS256'],
        claims_supported: ['sub', 'name'],
        code_challenge_methods_supported: ['S256'],
      }
      expect(config.issuer).to.equal('https://auth.example.com')
      expect(config.scopes_supported).to.include('openid')
    })
  })

  describe('JWK interface', () => {
    it('should allow creating conforming objects', () => {
      const jwk: import('../../../../src/modules/oauth_provider/types/oauth.types').JWK = {
        kty: 'RSA',
        use: 'sig',
        alg: 'RS256',
        kid: 'key-id-1',
        n: 'modulus-value',
        e: 'AQAB',
      }
      expect(jwk.kty).to.equal('RSA')
      expect(jwk.alg).to.equal('RS256')
    })
  })

  describe('PaginatedResponse interface', () => {
    it('should allow creating conforming objects', () => {
      const response: import('../../../../src/modules/oauth_provider/types/oauth.types').PaginatedResponse<string> = {
        data: ['item1', 'item2'],
        pagination: {
          page: 1,
          limit: 10,
          total: 2,
          totalPages: 1,
        },
      }
      expect(response.data).to.have.lengthOf(2)
      expect(response.pagination.totalPages).to.equal(1)
    })
  })
})
