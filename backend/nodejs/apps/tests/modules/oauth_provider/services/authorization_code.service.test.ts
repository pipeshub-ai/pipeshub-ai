import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { AuthorizationCodeService } from '../../../../src/modules/oauth_provider/services/authorization_code.service'
import { AuthorizationCode } from '../../../../src/modules/oauth_provider/schema/authorization_code.schema'
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema'
import { OAuthRefreshToken } from '../../../../src/modules/oauth_provider/schema/oauth.refresh_token.schema'
import { InvalidGrantError } from '../../../../src/libs/errors/oauth.errors'

describe('AuthorizationCodeService', () => {
  let service: AuthorizationCodeService
  let mockLogger: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
    service = new AuthorizationCodeService(mockLogger)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('generateCode', () => {
    it('should generate and store an authorization code', async () => {
      const createStub = sinon.stub(AuthorizationCode, 'create').resolves({} as any)

      const code = await service.generateCode(
        'client-1',
        new Types.ObjectId().toString(),
        new Types.ObjectId().toString(),
        'https://example.com/cb',
        ['org:read'],
      )

      expect(code).to.be.a('string')
      expect(code.length).to.equal(64) // 32 bytes hex
      expect(createStub.calledOnce).to.be.true
    })

    it('should store PKCE challenge when provided', async () => {
      const createStub = sinon.stub(AuthorizationCode, 'create').resolves({} as any)

      await service.generateCode(
        'client-1',
        new Types.ObjectId().toString(),
        new Types.ObjectId().toString(),
        'https://example.com/cb',
        ['org:read'],
        'challenge-value',
        'S256',
      )

      const createArgs = createStub.firstCall.args[0] as any
      expect(createArgs.codeChallenge).to.equal('challenge-value')
      expect(createArgs.codeChallengeMethod).to.equal('S256')
    })
  })

  describe('exchangeCode', () => {
    it('should exchange valid code', async () => {
      const userId = new Types.ObjectId()
      const orgId = new Types.ObjectId()
      const mockCode = {
        _id: new Types.ObjectId(),
        code: 'valid-code',
        clientId: 'client-1',
        userId,
        orgId,
        redirectUri: 'https://example.com/cb',
        scopes: ['org:read'],
        expiresAt: new Date(Date.now() + 600000),
        isUsed: false,
        codeChallenge: undefined,
        save: sinon.stub().resolves(),
      }
      sinon.stub(AuthorizationCode, 'findOne').resolves(mockCode as any)

      const result = await service.exchangeCode(
        'valid-code',
        'client-1',
        'https://example.com/cb',
      )

      expect(result.userId).to.equal(userId.toString())
      expect(result.orgId).to.equal(orgId.toString())
      expect(result.scopes).to.deep.equal(['org:read'])
      expect(mockCode.isUsed).to.be.true
    })

    it('should throw InvalidGrantError when code not found', async () => {
      sinon.stub(AuthorizationCode, 'findOne').resolves(null)
      try {
        await service.exchangeCode('bad-code', 'client-1', 'https://example.com/cb')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidGrantError)
      }
    })

    it('should throw InvalidGrantError and revoke tokens for code reuse', async () => {
      const userId = new Types.ObjectId()
      const mockCode = {
        _id: new Types.ObjectId(),
        code: 'used-code',
        clientId: 'client-1',
        userId,
        isUsed: true,
      }
      sinon.stub(AuthorizationCode, 'findOne').resolves(mockCode as any)
      sinon.stub(OAuthAccessToken, 'updateMany').resolves({} as any)
      sinon.stub(OAuthRefreshToken, 'updateMany').resolves({} as any)

      try {
        await service.exchangeCode('used-code', 'client-1', 'https://example.com/cb')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidGrantError)
        expect((error as InvalidGrantError).message).to.include('already been used')
      }
    })

    it('should throw InvalidGrantError when code is expired', async () => {
      const mockCode = {
        _id: new Types.ObjectId(),
        code: 'expired-code',
        clientId: 'client-1',
        userId: new Types.ObjectId(),
        expiresAt: new Date(Date.now() - 1000),
        isUsed: false,
      }
      sinon.stub(AuthorizationCode, 'findOne').resolves(mockCode as any)
      sinon.stub(AuthorizationCode, 'deleteOne').resolves({} as any)

      try {
        await service.exchangeCode('expired-code', 'client-1', 'https://example.com/cb')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidGrantError)
        expect((error as InvalidGrantError).message).to.include('expired')
      }
    })

    it('should throw InvalidGrantError for redirect URI mismatch', async () => {
      const mockCode = {
        _id: new Types.ObjectId(),
        code: 'valid-code',
        clientId: 'client-1',
        userId: new Types.ObjectId(),
        redirectUri: 'https://example.com/cb',
        expiresAt: new Date(Date.now() + 600000),
        isUsed: false,
      }
      sinon.stub(AuthorizationCode, 'findOne').resolves(mockCode as any)

      try {
        await service.exchangeCode('valid-code', 'client-1', 'https://other.com/cb')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidGrantError)
        expect((error as InvalidGrantError).message).to.include('mismatch')
      }
    })

    it('should throw InvalidGrantError when PKCE verifier is missing but challenge exists', async () => {
      const mockCode = {
        _id: new Types.ObjectId(),
        code: 'pkce-code',
        clientId: 'client-1',
        userId: new Types.ObjectId(),
        redirectUri: 'https://example.com/cb',
        expiresAt: new Date(Date.now() + 600000),
        isUsed: false,
        codeChallenge: 'some-challenge',
        codeChallengeMethod: 'S256',
      }
      sinon.stub(AuthorizationCode, 'findOne').resolves(mockCode as any)

      try {
        await service.exchangeCode('pkce-code', 'client-1', 'https://example.com/cb')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidGrantError)
        expect((error as InvalidGrantError).message).to.include('verifier required')
      }
    })

    it('should throw InvalidGrantError for invalid code verifier format', async () => {
      const mockCode = {
        _id: new Types.ObjectId(),
        code: 'pkce-code',
        clientId: 'client-1',
        userId: new Types.ObjectId(),
        redirectUri: 'https://example.com/cb',
        expiresAt: new Date(Date.now() + 600000),
        isUsed: false,
        codeChallenge: 'some-challenge',
        codeChallengeMethod: 'S256',
      }
      sinon.stub(AuthorizationCode, 'findOne').resolves(mockCode as any)

      try {
        // Too short verifier
        await service.exchangeCode('pkce-code', 'client-1', 'https://example.com/cb', 'short')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidGrantError)
      }
    })

    it('should verify PKCE with plain method', async () => {
      const verifier = 'a'.repeat(43) // valid length
      const mockCode = {
        _id: new Types.ObjectId(),
        code: 'pkce-code',
        clientId: 'client-1',
        userId: new Types.ObjectId(),
        orgId: new Types.ObjectId(),
        redirectUri: 'https://example.com/cb',
        scopes: ['org:read'],
        expiresAt: new Date(Date.now() + 600000),
        isUsed: false,
        codeChallenge: verifier, // plain method: challenge == verifier
        codeChallengeMethod: 'plain',
        save: sinon.stub().resolves(),
      }
      sinon.stub(AuthorizationCode, 'findOne').resolves(mockCode as any)

      const result = await service.exchangeCode(
        'pkce-code', 'client-1', 'https://example.com/cb', verifier,
      )
      expect(result).to.have.property('userId')
    })
  })

  describe('cleanupExpiredCodes', () => {
    it('should delete expired and used codes', async () => {
      sinon.stub(AuthorizationCode, 'deleteMany').resolves({ deletedCount: 5 } as any)
      const count = await service.cleanupExpiredCodes()
      expect(count).to.equal(5)
    })

    it('should return 0 when no codes to clean', async () => {
      sinon.stub(AuthorizationCode, 'deleteMany').resolves({ deletedCount: 0 } as any)
      const count = await service.cleanupExpiredCodes()
      expect(count).to.equal(0)
    })
  })

  describe('revokeCodesForUser', () => {
    it('should delete unused codes for user', async () => {
      const deleteStub = sinon.stub(AuthorizationCode, 'deleteMany').resolves({} as any)
      const userId = new Types.ObjectId().toString()
      await service.revokeCodesForUser(userId)
      expect(deleteStub.calledOnce).to.be.true
    })
  })

  describe('revokeCodesForApp', () => {
    it('should delete unused codes for app', async () => {
      const deleteStub = sinon.stub(AuthorizationCode, 'deleteMany').resolves({} as any)
      await service.revokeCodesForApp('client-1')
      expect(deleteStub.calledOnce).to.be.true
    })
  })

  describe('validateCodeVerifier (static)', () => {
    it('should accept valid verifiers (43-128 chars)', () => {
      expect(AuthorizationCodeService.validateCodeVerifier('a'.repeat(43))).to.be.true
      expect(AuthorizationCodeService.validateCodeVerifier('a'.repeat(128))).to.be.true
    })

    it('should reject too short verifiers', () => {
      expect(AuthorizationCodeService.validateCodeVerifier('a'.repeat(42))).to.be.false
    })

    it('should reject too long verifiers', () => {
      expect(AuthorizationCodeService.validateCodeVerifier('a'.repeat(129))).to.be.false
    })

    it('should reject verifiers with invalid characters', () => {
      expect(AuthorizationCodeService.validateCodeVerifier('a'.repeat(42) + '!')).to.be.false
    })

    it('should accept verifiers with valid special chars (- . _ ~)', () => {
      expect(AuthorizationCodeService.validateCodeVerifier('abcdef-._~'.repeat(5) + 'abc')).to.be.true
    })
  })

  describe('validateCodeChallenge (static)', () => {
    it('should accept valid challenges (43-128 chars)', () => {
      expect(AuthorizationCodeService.validateCodeChallenge('a'.repeat(43))).to.be.true
      expect(AuthorizationCodeService.validateCodeChallenge('a'.repeat(128))).to.be.true
    })

    it('should reject too short challenges', () => {
      expect(AuthorizationCodeService.validateCodeChallenge('a'.repeat(42))).to.be.false
    })

    it('should reject too long challenges', () => {
      expect(AuthorizationCodeService.validateCodeChallenge('a'.repeat(129))).to.be.false
    })

    it('should accept base64url characters', () => {
      expect(AuthorizationCodeService.validateCodeChallenge('ABCDEFGabcdefg0123456789-_' + 'a'.repeat(20))).to.be.true
    })
  })
})
