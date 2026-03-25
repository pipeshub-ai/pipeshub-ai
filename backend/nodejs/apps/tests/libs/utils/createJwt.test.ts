import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import {
  mailJwtGenerator,
  jwtGeneratorForForgotPasswordLink,
  jwtGeneratorForNewAccountPassword,
  refreshTokenJwtGenerator,
  iamJwtGenerator,
  slackJwtGenerator,
  iamUserLookupJwtGenerator,
  authJwtGenerator,
  fetchConfigJwtGenerator,
  scopedStorageServiceJwtGenerator,
} from '../../../src/libs/utils/createJwt'
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum'

describe('createJwt', () => {
  const secret = 'test-secret-key-12345'

  afterEach(() => {
    sinon.restore()
  })

  describe('mailJwtGenerator', () => {
    it('should generate a valid JWT with email and SEND_MAIL scope', () => {
      const token = mailJwtGenerator('user@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.email).to.equal('user@example.com')
      expect(decoded.scopes).to.deep.equal([TokenScopes.SEND_MAIL])
    })

    it('should set expiry to 1 hour', () => {
      const token = mailJwtGenerator('user@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      // exp - iat should be approximately 3600 seconds
      const diff = decoded.exp - decoded.iat
      expect(diff).to.equal(3600)
    })

    it('should produce different tokens for different emails', () => {
      const t1 = mailJwtGenerator('a@example.com', secret)
      const t2 = mailJwtGenerator('b@example.com', secret)
      expect(t1).not.to.equal(t2)
    })
  })

  describe('jwtGeneratorForForgotPasswordLink', () => {
    it('should return both passwordResetToken and mailAuthToken', () => {
      const result = jwtGeneratorForForgotPasswordLink(
        'user@example.com',
        'user-123',
        'org-456',
        secret,
      )
      expect(result).to.have.property('passwordResetToken').that.is.a('string')
      expect(result).to.have.property('mailAuthToken').that.is.a('string')
    })

    it('should embed correct claims in passwordResetToken', () => {
      const { passwordResetToken } = jwtGeneratorForForgotPasswordLink(
        'user@example.com',
        'user-123',
        'org-456',
        secret,
      )
      const decoded = jwt.verify(passwordResetToken, secret) as any
      expect(decoded.userEmail).to.equal('user@example.com')
      expect(decoded.userId).to.equal('user-123')
      expect(decoded.orgId).to.equal('org-456')
      expect(decoded.scopes).to.deep.equal([TokenScopes.PASSWORD_RESET])
    })

    it('should set passwordResetToken expiry to 20 minutes', () => {
      const { passwordResetToken } = jwtGeneratorForForgotPasswordLink(
        'user@example.com',
        'user-123',
        'org-456',
        secret,
      )
      const decoded = jwt.verify(passwordResetToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(20 * 60)
    })

    it('should embed correct claims in mailAuthToken', () => {
      const { mailAuthToken } = jwtGeneratorForForgotPasswordLink(
        'user@example.com',
        'user-123',
        'org-456',
        secret,
      )
      const decoded = jwt.verify(mailAuthToken, secret) as any
      expect(decoded.userEmail).to.equal('user@example.com')
      expect(decoded.scopes).to.deep.equal([TokenScopes.SEND_MAIL])
    })

    it('should set mailAuthToken expiry to 1 hour', () => {
      const { mailAuthToken } = jwtGeneratorForForgotPasswordLink(
        'user@example.com',
        'user-123',
        'org-456',
        secret,
      )
      const decoded = jwt.verify(mailAuthToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('jwtGeneratorForNewAccountPassword', () => {
    it('should return both passwordResetToken and mailAuthToken', () => {
      const result = jwtGeneratorForNewAccountPassword(
        'new@example.com',
        'user-new',
        'org-new',
        secret,
      )
      expect(result).to.have.property('passwordResetToken')
      expect(result).to.have.property('mailAuthToken')
    })

    it('should set passwordResetToken expiry to 48 hours', () => {
      const { passwordResetToken } = jwtGeneratorForNewAccountPassword(
        'new@example.com',
        'user-new',
        'org-new',
        secret,
      )
      const decoded = jwt.verify(passwordResetToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(48 * 3600)
    })

    it('should embed PASSWORD_RESET scope in passwordResetToken', () => {
      const { passwordResetToken } = jwtGeneratorForNewAccountPassword(
        'new@example.com',
        'user-new',
        'org-new',
        secret,
      )
      const decoded = jwt.verify(passwordResetToken, secret) as any
      expect(decoded.scopes).to.deep.equal([TokenScopes.PASSWORD_RESET])
    })

    it('should set mailAuthToken expiry to 1 hour', () => {
      const { mailAuthToken } = jwtGeneratorForNewAccountPassword(
        'new@example.com',
        'user-new',
        'org-new',
        secret,
      )
      const decoded = jwt.verify(mailAuthToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('refreshTokenJwtGenerator', () => {
    it('should generate a token with userId, orgId, and TOKEN_REFRESH scope', () => {
      const token = refreshTokenJwtGenerator('user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.userId).to.equal('user-1')
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.scopes).to.deep.equal([TokenScopes.TOKEN_REFRESH])
    })

    it('should default expiry to 720h (30 days) when env var is not set', () => {
      delete process.env.REFRESH_TOKEN_EXPIRY
      const token = refreshTokenJwtGenerator('user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(30 * 24 * 3600)
    })

    it('should use REFRESH_TOKEN_EXPIRY env var when set', () => {
      const originalVal = process.env.REFRESH_TOKEN_EXPIRY
      process.env.REFRESH_TOKEN_EXPIRY = '1h'
      try {
        const token = refreshTokenJwtGenerator('user-1', 'org-1', secret)
        const decoded = jwt.verify(token, secret) as any
        expect(decoded.exp - decoded.iat).to.equal(3600)
      } finally {
        if (originalVal !== undefined) {
          process.env.REFRESH_TOKEN_EXPIRY = originalVal
        } else {
          delete process.env.REFRESH_TOKEN_EXPIRY
        }
      }
    })
  })

  describe('iamJwtGenerator', () => {
    it('should generate a token with email and USER_LOOKUP scope', () => {
      const token = iamJwtGenerator('admin@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.email).to.equal('admin@example.com')
      expect(decoded.scopes).to.deep.equal([TokenScopes.USER_LOOKUP])
    })

    it('should set expiry to 1 hour', () => {
      const token = iamJwtGenerator('admin@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('slackJwtGenerator', () => {
    it('should generate a token with email and default CONVERSATION_CREATE scope', () => {
      const token = slackJwtGenerator('slack@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.email).to.equal('slack@example.com')
      expect(decoded.scopes).to.deep.equal([TokenScopes.CONVERSATION_CREATE])
    })

    it('should use custom scopes when provided', () => {
      const customScopes = [TokenScopes.SEND_MAIL, TokenScopes.USER_LOOKUP]
      const token = slackJwtGenerator('slack@example.com', secret, customScopes)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.scopes).to.deep.equal(customScopes)
    })

    it('should set expiry to 1 hour', () => {
      const token = slackJwtGenerator('slack@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('iamUserLookupJwtGenerator', () => {
    it('should generate a token with userId, orgId, and USER_LOOKUP scope', () => {
      const token = iamUserLookupJwtGenerator('user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.userId).to.equal('user-1')
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.scopes).to.deep.equal([TokenScopes.USER_LOOKUP])
    })

    it('should set expiry to 1 hour', () => {
      const token = iamUserLookupJwtGenerator('user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('authJwtGenerator', () => {
    it('should generate a token with all provided claims', () => {
      const token = authJwtGenerator(
        secret,
        'user@example.com',
        'user-1',
        'org-1',
        'John Doe',
        'premium',
      )
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.email).to.equal('user@example.com')
      expect(decoded.userId).to.equal('user-1')
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.fullName).to.equal('John Doe')
      expect(decoded.accountType).to.equal('premium')
    })

    it('should handle null/undefined optional claims', () => {
      const token = authJwtGenerator(secret, null, null, null, null, null)
      const decoded = jwt.verify(token, secret) as any
      // null values are still included in the payload
      expect(decoded).to.have.property('email')
      expect(decoded).to.have.property('userId')
    })

    it('should handle no optional claims', () => {
      const token = authJwtGenerator(secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded).to.exist
    })

    it('should default expiry to 24h when ACCESS_TOKEN_EXPIRY env var is not set', () => {
      delete process.env.ACCESS_TOKEN_EXPIRY
      const token = authJwtGenerator(secret, 'user@example.com')
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(24 * 3600)
    })

    it('should use ACCESS_TOKEN_EXPIRY env var when set', () => {
      const originalVal = process.env.ACCESS_TOKEN_EXPIRY
      process.env.ACCESS_TOKEN_EXPIRY = '2h'
      try {
        const token = authJwtGenerator(secret, 'user@example.com')
        const decoded = jwt.verify(token, secret) as any
        expect(decoded.exp - decoded.iat).to.equal(2 * 3600)
      } finally {
        if (originalVal !== undefined) {
          process.env.ACCESS_TOKEN_EXPIRY = originalVal
        } else {
          delete process.env.ACCESS_TOKEN_EXPIRY
        }
      }
    })
  })

  describe('fetchConfigJwtGenerator', () => {
    it('should generate a token with userId, orgId, and FETCH_CONFIG scope', () => {
      const token = fetchConfigJwtGenerator('user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.userId).to.equal('user-1')
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.scopes).to.deep.equal([TokenScopes.FETCH_CONFIG])
    })

    it('should set expiry to 1 hour', () => {
      const token = fetchConfigJwtGenerator('user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('scopedStorageServiceJwtGenerator', () => {
    it('should generate a token with orgId and STORAGE_TOKEN scope', () => {
      const token = scopedStorageServiceJwtGenerator('org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.scopes).to.deep.equal([TokenScopes.STORAGE_TOKEN])
    })

    it('should set expiry to 1 hour', () => {
      const token = scopedStorageServiceJwtGenerator('org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('token verification with wrong secret', () => {
    it('should throw JsonWebTokenError when verifying with wrong secret', () => {
      const token = mailJwtGenerator('user@example.com', secret)
      expect(() => jwt.verify(token, 'wrong-secret')).to.throw(
        jwt.JsonWebTokenError,
      )
    })
  })
})
