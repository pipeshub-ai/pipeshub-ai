import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Types } from 'mongoose'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema'
import { OAuthRefreshToken } from '../../../../src/modules/oauth_provider/schema/oauth.refresh_token.schema'
import {
  InvalidTokenError,
  ExpiredTokenError,
} from '../../../../src/libs/errors/oauth.errors'

describe('OAuthTokenService', () => {
  let service: OAuthTokenService
  let mockLogger: any
  const testSecret = 'test-secret-for-jwt-signing-minimum-length'
  const testIssuer = 'http://test-issuer.com'

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
    const jwtConfig = { secret: testSecret } as any
    service = new OAuthTokenService(mockLogger, jwtConfig, testIssuer)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('getAlgorithm', () => {
    it('should return HS256 for secret-based config', () => {
      expect(service.getAlgorithm()).to.equal('HS256')
    })
  })

  describe('getKeyId', () => {
    it('should return undefined for HS256', () => {
      expect(service.getKeyId()).to.be.undefined
    })
  })

  describe('getPublicKey', () => {
    it('should return undefined for HS256 config', () => {
      expect(service.getPublicKey()).to.be.undefined
    })
  })

  describe('generateTokens', () => {
    const mockApp = {
      clientId: 'client-1',
      accessTokenLifetime: 3600,
      refreshTokenLifetime: 2592000,
      createdBy: new Types.ObjectId(),
    } as any

    it('should generate access token without refresh token', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const result = await service.generateTokens(
        mockApp,
        null,
        new Types.ObjectId().toString(),
        ['org:read'],
        false,
      )

      expect(result.accessToken).to.be.a('string')
      expect(result.tokenType).to.equal('Bearer')
      expect(result.expiresIn).to.equal(3600)
      expect(result.refreshToken).to.be.undefined
    })

    it('should generate access and refresh tokens when offline_access scope present', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)
      sinon.stub(OAuthRefreshToken, 'create').resolves({} as any)

      const userId = new Types.ObjectId().toString()
      const result = await service.generateTokens(
        mockApp,
        userId,
        new Types.ObjectId().toString(),
        ['org:read', 'offline_access'],
        true,
      )

      expect(result.accessToken).to.be.a('string')
      expect(result.refreshToken).to.be.a('string')
    })

    it('should not generate refresh token without offline_access scope', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const userId = new Types.ObjectId().toString()
      const result = await service.generateTokens(
        mockApp,
        userId,
        new Types.ObjectId().toString(),
        ['org:read'],
        true,
      )

      expect(result.refreshToken).to.be.undefined
    })

    it('should not generate refresh token without userId', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const result = await service.generateTokens(
        mockApp,
        null,
        new Types.ObjectId().toString(),
        ['org:read', 'offline_access'],
        true,
      )

      expect(result.refreshToken).to.be.undefined
    })
  })

  describe('verifyAccessToken', () => {
    it('should verify a valid access token', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      sinon.stub(OAuthAccessToken, 'findOne').resolves({ isRevoked: false } as any)

      const result = await service.verifyAccessToken(token)
      expect(result.userId).to.equal('user-1')
      expect(result.client_id).to.equal('client-1')
    })

    it('should throw InvalidTokenError for refresh token used as access token', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        isRefreshToken: true,
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      try {
        await service.verifyAccessToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
      }
    })

    it('should throw InvalidTokenError for revoked token', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      sinon.stub(OAuthAccessToken, 'findOne').resolves(null)

      try {
        await service.verifyAccessToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
      }
    })

    it('should throw ExpiredTokenError for expired token', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        exp: Math.floor(Date.now() / 1000) - 100,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        jti: 'jti-1',
      }
      // Sign with noTimestamp to allow custom exp
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256', noTimestamp: true })

      try {
        await service.verifyAccessToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(ExpiredTokenError)
      }
    })

    it('should throw InvalidTokenError for invalid token', async () => {
      try {
        await service.verifyAccessToken('invalid-token')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
      }
    })
  })

  describe('verifyRefreshToken', () => {
    it('should verify a valid refresh token', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read offline_access',
        client_id: 'client-1',
        tokenType: 'oauth',
        isRefreshToken: true,
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      sinon.stub(OAuthRefreshToken, 'findOne').resolves({ isRevoked: false } as any)

      const result = await service.verifyRefreshToken(token)
      expect(result.isRefreshToken).to.be.true
    })

    it('should throw InvalidTokenError for access token used as refresh', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      try {
        await service.verifyRefreshToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
      }
    })
  })

  describe('revokeToken', () => {
    it('should revoke an access token', async () => {
      sinon.stub(OAuthAccessToken, 'updateOne').resolves({ modifiedCount: 1 } as any)

      const result = await service.revokeToken('token', 'client-1', 'access_token')
      expect(result).to.be.true
    })

    it('should revoke a refresh token', async () => {
      sinon.stub(OAuthAccessToken, 'updateOne').resolves({ modifiedCount: 0 } as any)
      sinon.stub(OAuthRefreshToken, 'updateOne').resolves({ modifiedCount: 1 } as any)

      const result = await service.revokeToken('token', 'client-1')
      expect(result).to.be.true
    })

    it('should return false when token not found', async () => {
      sinon.stub(OAuthAccessToken, 'updateOne').resolves({ modifiedCount: 0 } as any)
      sinon.stub(OAuthRefreshToken, 'updateOne').resolves({ modifiedCount: 0 } as any)

      const result = await service.revokeToken('token', 'client-1')
      expect(result).to.be.false
    })

    it('should only check refresh token when hint is refresh_token', async () => {
      const refreshStub = sinon.stub(OAuthRefreshToken, 'updateOne').resolves({ modifiedCount: 1 } as any)

      const result = await service.revokeToken('token', 'client-1', 'refresh_token')
      expect(result).to.be.true
      expect(refreshStub.calledOnce).to.be.true
    })
  })

  describe('revokeAllTokensForApp', () => {
    it('should revoke all access and refresh tokens for an app', async () => {
      sinon.stub(OAuthAccessToken, 'updateMany').resolves({} as any)
      sinon.stub(OAuthRefreshToken, 'updateMany').resolves({} as any)

      await service.revokeAllTokensForApp('client-1')
      expect(mockLogger.info.called).to.be.true
    })
  })

  describe('revokeAllTokensForUser', () => {
    it('should revoke all tokens for a user in an app', async () => {
      sinon.stub(OAuthAccessToken, 'updateMany').resolves({} as any)
      sinon.stub(OAuthRefreshToken, 'updateMany').resolves({} as any)

      await service.revokeAllTokensForUser('client-1', new Types.ObjectId().toString())
      expect(mockLogger.info.called).to.be.true
    })
  })

  describe('introspectToken', () => {
    it('should return active for valid token owned by client', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      sinon.stub(OAuthAccessToken, 'findOne').resolves({
        isRevoked: false,
        userId: new Types.ObjectId(),
      } as any)

      const result = await service.introspectToken(token, 'client-1')
      expect(result.active).to.be.true
      expect(result.client_id).to.equal('client-1')
    })

    it('should return inactive for different client', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      const result = await service.introspectToken(token, 'different-client')
      expect(result.active).to.be.false
    })

    it('should return inactive for revoked token', async () => {
      const payload = {
        userId: 'user-1',
        orgId: 'org-1',
        iss: testIssuer,
        scope: 'org:read',
        client_id: 'client-1',
        tokenType: 'oauth',
        jti: 'jti-1',
      }
      const token = jwt.sign(payload, testSecret, { algorithm: 'HS256' })

      sinon.stub(OAuthAccessToken, 'findOne').resolves(null)

      const result = await service.introspectToken(token, 'client-1')
      expect(result.active).to.be.false
    })

    it('should return inactive for invalid token', async () => {
      const result = await service.introspectToken('invalid-token', 'client-1')
      expect(result.active).to.be.false
    })
  })

  describe('listTokensForApp', () => {
    it('should return combined access and refresh tokens sorted by date', async () => {
      const now = new Date()
      const accessChainable = {
        sort: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([{
          _id: new Types.ObjectId(),
          userId: new Types.ObjectId(),
          scopes: ['org:read'],
          createdAt: now,
          expiresAt: new Date(now.getTime() + 3600000),
          isRevoked: false,
        }]),
      }
      const refreshChainable = {
        sort: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([{
          _id: new Types.ObjectId(),
          userId: new Types.ObjectId(),
          scopes: ['org:read', 'offline_access'],
          createdAt: new Date(now.getTime() + 1000),
          expiresAt: new Date(now.getTime() + 2592000000),
          isRevoked: false,
        }]),
      }
      sinon.stub(OAuthAccessToken, 'find').returns(accessChainable as any)
      sinon.stub(OAuthRefreshToken, 'find').returns(refreshChainable as any)

      const tokens = await service.listTokensForApp('client-1')
      expect(tokens).to.have.lengthOf(2)
      // Should be sorted by createdAt desc
      expect(tokens[0].tokenType).to.equal('refresh')
    })
  })

  describe('decodeToken', () => {
    it('should decode a valid token without verification', () => {
      const token = jwt.sign({ userId: 'user-1' }, 'some-secret')
      const result = service.decodeToken(token)
      expect(result).to.have.property('userId', 'user-1')
    })

    it('should return null for invalid token', () => {
      const result = service.decodeToken('not-a-token')
      expect(result).to.be.null
    })
  })
})
