import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Types } from 'mongoose'
import {
  InvalidTokenError,
  ExpiredTokenError,
} from '../../../../src/libs/errors/oauth.errors'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema'
import { OAuthRefreshToken } from '../../../../src/modules/oauth_provider/schema/oauth.refresh_token.schema'
import { createMockLogger } from '../../../helpers/mock-logger'

const VALID_USER_ID = new Types.ObjectId().toString()
const VALID_ORG_ID = new Types.ObjectId().toString()

describe('OAuthTokenService - branch coverage', () => {
  let service: OAuthTokenService
  let mockLogger: any

  beforeEach(() => {
    mockLogger = createMockLogger()
    // Create with HMAC key config
    service = new OAuthTokenService(
      mockLogger as any,
      { algorithm: 'HS256', secret: 'test-secret-key-for-signing-tokens-at-least-32-bytes' } as any,
      'https://test-issuer.com',
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  // =========================================================================
  // getAlgorithm, getKeyId, getPublicKey
  // =========================================================================
  describe('getters', () => {
    it('should return algorithm', () => {
      const alg = service.getAlgorithm()
      expect(alg).to.be.a('string')
    })

    it('should return keyId (may be undefined for HMAC)', () => {
      const keyId = service.getKeyId()
      // For HMAC config, keyId is undefined
      expect(keyId).to.satisfy((v: any) => v === undefined || typeof v === 'string')
    })

    it('should return publicKey (undefined for HMAC)', () => {
      const pubKey = service.getPublicKey()
      expect(pubKey).to.be.undefined
    })
  })

  // =========================================================================
  // generateTokens - branches
  // =========================================================================
  describe('generateTokens', () => {
    it('should generate access token without refresh token when includeRefreshToken is false', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const app = {
        clientId: 'client-1',
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 86400,
        createdBy: new Types.ObjectId(),
      } as any

      const result = await service.generateTokens(app, VALID_USER_ID, VALID_ORG_ID, ['user:read'], false)

      expect(result.accessToken).to.be.a('string')
      expect(result.refreshToken).to.be.undefined
      expect(result.tokenType).to.equal('Bearer')
    })

    it('should generate refresh token when includeRefreshToken is true, userId exists, and scope includes offline_access', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)
      sinon.stub(OAuthRefreshToken, 'create').resolves({} as any)

      const app = {
        clientId: 'client-1',
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 86400,
        createdBy: new Types.ObjectId(),
      } as any

      const result = await service.generateTokens(
        app, VALID_USER_ID, VALID_ORG_ID, ['user:read', 'offline_access'], true,
      )

      expect(result.accessToken).to.be.a('string')
      expect(result.refreshToken).to.be.a('string')
    })

    it('should NOT generate refresh token when userId is null', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const app = {
        clientId: 'client-1',
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 86400,
        createdBy: new Types.ObjectId(),
      } as any

      const result = await service.generateTokens(app, null, VALID_ORG_ID, ['offline_access'], true)

      // userId null means no refresh token (even though includeRefreshToken is true)
      expect(result.refreshToken).to.be.undefined
    })

    it('should NOT generate refresh token when scopes dont include offline_access', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const app = {
        clientId: 'client-1',
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 86400,
        createdBy: new Types.ObjectId(),
      } as any

      const result = await service.generateTokens(app, VALID_USER_ID, VALID_ORG_ID, ['user:read'], true)

      expect(result.refreshToken).to.be.undefined
    })

    it('should use app creator as JWT userId when userId is null (client_credentials)', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const creator = new Types.ObjectId()
      const app = {
        clientId: 'client-1',
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 86400,
        createdBy: creator,
      } as any

      const result = await service.generateTokens(app, null, VALID_ORG_ID, ['user:read'], false)

      const decoded = jwt.decode(result.accessToken) as any
      expect(decoded.userId).to.equal(creator.toString())
    })

    it('should include fullName and accountType when provided', async () => {
      sinon.stub(OAuthAccessToken, 'create').resolves({} as any)

      const app = {
        clientId: 'client-1',
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 86400,
        createdBy: new Types.ObjectId(),
      } as any

      const result = await service.generateTokens(
        app, VALID_USER_ID, VALID_ORG_ID, ['user:read'], false, 'Test User', 'premium',
      )

      const decoded = jwt.decode(result.accessToken) as any
      expect(decoded.fullName).to.equal('Test User')
      expect(decoded.accountType).to.equal('premium')
    })
  })

  // =========================================================================
  // verifyAccessToken - branches
  // =========================================================================
  describe('verifyAccessToken', () => {
    it('should throw InvalidTokenError when token is a refresh token', async () => {
      // Create a token with isRefreshToken: true
      const token = jwt.sign({ isRefreshToken: true, userId: 'u1', orgId: 'o1' }, 'test-secret-key-for-signing-tokens-at-least-32-bytes')

      sinon.stub(OAuthAccessToken, 'findOne').resolves({} as any)

      try {
        await service.verifyAccessToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
        expect((error as Error).message).to.equal('Invalid token type')
      }
    })

    it('should throw InvalidTokenError when token is revoked (not found)', async () => {
      const token = jwt.sign({ userId: 'u1', orgId: 'o1' }, 'test-secret-key-for-signing-tokens-at-least-32-bytes')

      sinon.stub(OAuthAccessToken, 'findOne').resolves(null)

      try {
        await service.verifyAccessToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
        expect((error as Error).message).to.equal('Token has been revoked')
      }
    })

    it('should throw ExpiredTokenError for expired tokens', async () => {
      const token = jwt.sign(
        { userId: 'u1', orgId: 'o1', exp: Math.floor(Date.now() / 1000) - 100 },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      try {
        await service.verifyAccessToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(ExpiredTokenError)
      }
    })

    it('should throw InvalidTokenError for malformed tokens', async () => {
      try {
        await service.verifyAccessToken('not-a-valid-jwt')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
      }
    })

    it('should return payload for valid non-revoked access token', async () => {
      const token = jwt.sign({ userId: 'u1', orgId: 'o1' }, 'test-secret-key-for-signing-tokens-at-least-32-bytes')

      sinon.stub(OAuthAccessToken, 'findOne').resolves({ tokenHash: 'hash' } as any)

      const payload = await service.verifyAccessToken(token)
      expect(payload.userId).to.equal('u1')
      expect(payload.orgId).to.equal('o1')
    })
  })

  // =========================================================================
  // verifyRefreshToken - branches
  // =========================================================================
  describe('verifyRefreshToken', () => {
    it('should throw InvalidTokenError when token is not a refresh token', async () => {
      const token = jwt.sign({ userId: 'u1', orgId: 'o1' }, 'test-secret-key-for-signing-tokens-at-least-32-bytes')

      try {
        await service.verifyRefreshToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
        expect((error as Error).message).to.equal('Invalid token type')
      }
    })

    it('should throw InvalidTokenError when refresh token is revoked', async () => {
      const token = jwt.sign({ isRefreshToken: true, userId: 'u1', orgId: 'o1' }, 'test-secret-key-for-signing-tokens-at-least-32-bytes')

      sinon.stub(OAuthRefreshToken, 'findOne').resolves(null)

      try {
        await service.verifyRefreshToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
        expect((error as Error).message).to.equal('Refresh token has been revoked')
      }
    })

    it('should throw ExpiredTokenError for expired refresh tokens', async () => {
      const token = jwt.sign(
        { isRefreshToken: true, userId: 'u1', orgId: 'o1', exp: Math.floor(Date.now() / 1000) - 100 },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      try {
        await service.verifyRefreshToken(token)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(ExpiredTokenError)
      }
    })

    it('should throw InvalidTokenError for malformed refresh tokens', async () => {
      try {
        await service.verifyRefreshToken('invalid-jwt')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
      }
    })
  })

  // =========================================================================
  // revokeToken - branches
  // =========================================================================
  describe('revokeToken', () => {
    it('should revoke access token when tokenType is access_token', async () => {
      sinon.stub(OAuthAccessToken, 'updateOne').resolves({ modifiedCount: 1 } as any)

      const result = await service.revokeToken('token', 'client-1', 'access_token')
      expect(result).to.be.true
    })

    it('should revoke refresh token when tokenType is refresh_token', async () => {
      sinon.stub(OAuthRefreshToken, 'updateOne').resolves({ modifiedCount: 1 } as any)

      const result = await service.revokeToken('token', 'client-1', 'refresh_token')
      expect(result).to.be.true
    })

    it('should try both when tokenType is not specified', async () => {
      sinon.stub(OAuthAccessToken, 'updateOne').resolves({ modifiedCount: 0 } as any)
      sinon.stub(OAuthRefreshToken, 'updateOne').resolves({ modifiedCount: 1 } as any)

      const result = await service.revokeToken('token', 'client-1')
      expect(result).to.be.true
    })

    it('should return false when no token is revoked', async () => {
      sinon.stub(OAuthAccessToken, 'updateOne').resolves({ modifiedCount: 0 } as any)
      sinon.stub(OAuthRefreshToken, 'updateOne').resolves({ modifiedCount: 0 } as any)

      const result = await service.revokeToken('token', 'client-1')
      expect(result).to.be.false
    })

    it('should return true when access token is found without type hint', async () => {
      sinon.stub(OAuthAccessToken, 'updateOne').resolves({ modifiedCount: 1 } as any)

      const result = await service.revokeToken('token', 'client-1')
      expect(result).to.be.true
    })
  })

  // =========================================================================
  // refreshTokens - scope narrowing
  // =========================================================================
  describe('refreshTokens', () => {
    it('should throw InvalidTokenError when stored refresh token not found', async () => {
      const refreshToken = jwt.sign(
        { isRefreshToken: true, userId: 'u1', orgId: 'o1' },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      sinon.stub(OAuthRefreshToken, 'findOne')
        .onFirstCall().resolves({} as any) // verifyRefreshToken check
        .onSecondCall().resolves(null) // refreshTokens check

      try {
        await service.refreshTokens({ clientId: 'c1' } as any, refreshToken)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidTokenError)
      }
    })
  })

  // =========================================================================
  // introspectToken - branches
  // =========================================================================
  describe('introspectToken', () => {
    it('should return inactive for token with different client_id', async () => {
      const token = jwt.sign(
        { client_id: 'client-1', userId: 'u1', orgId: 'o1' },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      const result = await service.introspectToken(token, 'other-client')
      expect(result.active).to.be.false
    })

    it('should return inactive for revoked token', async () => {
      const token = jwt.sign(
        { client_id: 'client-1', userId: 'u1', orgId: 'o1' },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      sinon.stub(OAuthAccessToken, 'findOne').resolves(null)

      const result = await service.introspectToken(token, 'client-1')
      expect(result.active).to.be.false
    })

    it('should return active with token info for valid access token', async () => {
      const token = jwt.sign(
        { client_id: 'client-1', userId: 'u1', orgId: 'o1', scope: 'user:read', jti: 'j1' },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      sinon.stub(OAuthAccessToken, 'findOne').resolves({
        userId: new Types.ObjectId(),
      } as any)

      const result = await service.introspectToken(token, 'client-1')
      expect(result.active).to.be.true
      expect(result.client_id).to.equal('client-1')
      expect(result.token_type).to.equal('Bearer')
    })

    it('should check refresh token collection for refresh tokens', async () => {
      const token = jwt.sign(
        { client_id: 'client-1', userId: 'u1', orgId: 'o1', scope: 'user:read', isRefreshToken: true },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      sinon.stub(OAuthRefreshToken, 'findOne').resolves({
        userId: new Types.ObjectId(),
      } as any)

      const result = await service.introspectToken(token, 'client-1')
      expect(result.active).to.be.true
      expect(result.token_type).to.equal('refresh_token')
    })

    it('should return inactive for invalid/expired tokens', async () => {
      const result = await service.introspectToken('invalid-token', 'client-1')
      expect(result.active).to.be.false
    })

    it('should include username when storedToken has userId', async () => {
      const token = jwt.sign(
        { client_id: 'client-1', userId: 'u1', orgId: 'o1', scope: 'user:read' },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      const userId = new Types.ObjectId()
      sinon.stub(OAuthAccessToken, 'findOne').resolves({ userId } as any)

      const result = await service.introspectToken(token, 'client-1')
      expect(result.username).to.equal(userId.toString())
    })

    it('should not include username when storedToken has no userId', async () => {
      const token = jwt.sign(
        { client_id: 'client-1', userId: 'u1', orgId: 'o1', scope: 'user:read' },
        'test-secret-key-for-signing-tokens-at-least-32-bytes',
      )

      sinon.stub(OAuthAccessToken, 'findOne').resolves({ userId: null } as any)

      const result = await service.introspectToken(token, 'client-1')
      expect(result.username).to.be.undefined
    })
  })

  // =========================================================================
  // decodeToken
  // =========================================================================
  describe('decodeToken', () => {
    it('should decode a valid token without verification', () => {
      const token = jwt.sign({ userId: 'u1' }, 'any-secret')
      const result = service.decodeToken(token)
      expect(result).to.exist
      expect(result!.userId).to.equal('u1')
    })

    it('should return null for invalid token', () => {
      const result = service.decodeToken('')
      expect(result).to.be.null
    })
  })
})
