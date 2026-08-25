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
  jwtGeneratorForValidateEmailLink,
  jwtGeneratorForOrgEmailVerification,
  jwtGeneratorForOtpMail,
  jwtGeneratorForEmailVerified,
  jwtGeneratorForMailAuth,
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
        'admin',
      )
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.email).to.equal('user@example.com')
      expect(decoded.userId).to.equal('user-1')
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.fullName).to.equal('John Doe')
      expect(decoded.accountType).to.equal('premium')
      expect(decoded.role).to.equal('admin')
    })

    it('should omit role claim when role is not provided', () => {
      const token = authJwtGenerator(
        secret,
        'user@example.com',
        'user-1',
        'org-1',
        'John Doe',
        'premium',
      )
      const decoded = jwt.verify(token, secret) as any
      expect(decoded).to.not.have.property('role')
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

  describe('scopedStorageServiceJwtGenerator with userId', () => {
    it('should include userId when provided', () => {
      const token = scopedStorageServiceJwtGenerator('org-1', secret, 'user-42')
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.userId).to.equal('user-42')
      expect(decoded.scopes).to.deep.equal([TokenScopes.STORAGE_TOKEN])
    })

    it('should omit userId when not provided', () => {
      const token = scopedStorageServiceJwtGenerator('org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded).to.not.have.property('userId')
    })
  })

  describe('jwtGeneratorForValidateEmailLink', () => {
    it('should return both validateEmailToken and mailAuthToken', () => {
      const result = jwtGeneratorForValidateEmailLink(
        'old@example.com',
        'new@example.com',
        'user-1',
        'org-1',
        secret,
      )
      expect(result).to.have.property('validateEmailToken').that.is.a('string')
      expect(result).to.have.property('mailAuthToken').that.is.a('string')
    })

    it('should embed correct claims in validateEmailToken', () => {
      const { validateEmailToken } = jwtGeneratorForValidateEmailLink(
        'old@example.com',
        'new@example.com',
        'user-1',
        'org-1',
        secret,
      )
      const decoded = jwt.verify(validateEmailToken, secret) as any
      expect(decoded.userEmail).to.equal('old@example.com')
      expect(decoded.newEmail).to.equal('new@example.com')
      expect(decoded.userId).to.equal('user-1')
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.scopes).to.deep.equal([TokenScopes.VALIDATE_EMAIL])
    })

    it('should set validateEmailToken expiry to 20 minutes', () => {
      const { validateEmailToken } = jwtGeneratorForValidateEmailLink(
        'old@example.com',
        'new@example.com',
        'user-1',
        'org-1',
        secret,
      )
      const decoded = jwt.verify(validateEmailToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(20 * 60)
    })

    it('should set mailAuthToken expiry to 1 hour', () => {
      const { mailAuthToken } = jwtGeneratorForValidateEmailLink(
        'old@example.com',
        'new@example.com',
        'user-1',
        'org-1',
        secret,
      )
      const decoded = jwt.verify(mailAuthToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('jwtGeneratorForOrgEmailVerification', () => {
    it('should return both orgVerificationToken and mailAuthToken', () => {
      const result = jwtGeneratorForOrgEmailVerification(
        'org-1',
        'contact@example.com',
        secret,
        'admin-org-1',
      )
      expect(result).to.have.property('orgVerificationToken').that.is.a('string')
      expect(result).to.have.property('mailAuthToken').that.is.a('string')
    })

    it('should embed correct claims in orgVerificationToken', () => {
      const { orgVerificationToken } = jwtGeneratorForOrgEmailVerification(
        'org-1',
        'contact@example.com',
        secret,
        'admin-org-1',
      )
      const decoded = jwt.verify(orgVerificationToken, secret) as any
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.contactEmail).to.equal('contact@example.com')
      expect(decoded.scopes).to.deep.equal([TokenScopes.ORG_EMAIL_VERIFY])
    })

    it('should set orgVerificationToken expiry to 24 hours', () => {
      const { orgVerificationToken } = jwtGeneratorForOrgEmailVerification(
        'org-1',
        'contact@example.com',
        secret,
        'admin-org-1',
      )
      const decoded = jwt.verify(orgVerificationToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(24 * 3600)
    })

    it('should use smtpOrgId in mailAuthToken', () => {
      const { mailAuthToken } = jwtGeneratorForOrgEmailVerification(
        'org-1',
        'contact@example.com',
        secret,
        'admin-org-1',
      )
      const decoded = jwt.verify(mailAuthToken, secret) as any
      expect(decoded.orgId).to.equal('admin-org-1')
      expect(decoded.scopes).to.deep.equal([TokenScopes.SEND_MAIL])
    })

    it('should set mailAuthToken expiry to 25 hours', () => {
      const { mailAuthToken } = jwtGeneratorForOrgEmailVerification(
        'org-1',
        'contact@example.com',
        secret,
        'admin-org-1',
      )
      const decoded = jwt.verify(mailAuthToken, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(25 * 3600)
    })
  })

  describe('jwtGeneratorForOtpMail', () => {
    it('should generate a token with email, orgId, and SEND_MAIL scope', () => {
      const token = jwtGeneratorForOtpMail('user@example.com', 'admin-org', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.email).to.equal('user@example.com')
      expect(decoded.orgId).to.equal('admin-org')
      expect(decoded.scopes).to.deep.equal([TokenScopes.SEND_MAIL])
    })

    it('should set expiry to 10 minutes', () => {
      const token = jwtGeneratorForOtpMail('user@example.com', 'admin-org', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(10 * 60)
    })
  })

  describe('jwtGeneratorForEmailVerified', () => {
    it('should generate a token with EMAIL_VERIFIED scope', () => {
      const token = jwtGeneratorForEmailVerified('user@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.email).to.equal('user@example.com')
      expect(decoded.scopes).to.deep.equal([TokenScopes.EMAIL_VERIFIED])
    })

    it('should include hashProof when provided', () => {
      const token = jwtGeneratorForEmailVerified('user@example.com', secret, ['hash1', 'hash2'])
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.hashProof).to.deep.equal(['hash1', 'hash2'])
    })

    it('should default hashProof to empty array', () => {
      const token = jwtGeneratorForEmailVerified('user@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.hashProof).to.deep.equal([])
    })

    it('should default expiry to 30d when EMAIL_VERIFIED_TOKEN_EXPIRY env var is not set', () => {
      delete process.env.EMAIL_VERIFIED_TOKEN_EXPIRY
      const token = jwtGeneratorForEmailVerified('user@example.com', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(30 * 24 * 3600)
    })

    it('should use EMAIL_VERIFIED_TOKEN_EXPIRY env var when set', () => {
      const originalVal = process.env.EMAIL_VERIFIED_TOKEN_EXPIRY
      process.env.EMAIL_VERIFIED_TOKEN_EXPIRY = '1h'
      try {
        const token = jwtGeneratorForEmailVerified('user@example.com', secret)
        const decoded = jwt.verify(token, secret) as any
        expect(decoded.exp - decoded.iat).to.equal(3600)
      } finally {
        if (originalVal !== undefined) {
          process.env.EMAIL_VERIFIED_TOKEN_EXPIRY = originalVal
        } else {
          delete process.env.EMAIL_VERIFIED_TOKEN_EXPIRY
        }
      }
    })
  })

  describe('jwtGeneratorForMailAuth', () => {
    it('should generate a token with SEND_MAIL scope', () => {
      const token = jwtGeneratorForMailAuth('user@example.com', 'user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.userEmail).to.equal('user@example.com')
      expect(decoded.userId).to.equal('user-1')
      expect(decoded.orgId).to.equal('org-1')
      expect(decoded.scopes).to.deep.equal([TokenScopes.SEND_MAIL])
    })

    it('should set expiry to 1 hour', () => {
      const token = jwtGeneratorForMailAuth('user@example.com', 'user-1', 'org-1', secret)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.exp - decoded.iat).to.equal(3600)
    })
  })

  describe('authJwtGenerator role variants', () => {
    it('should include role=member when role is member', () => {
      const token = authJwtGenerator(secret, 'e@x.com', 'u1', 'o1', 'Name', 'free', 'member')
      const decoded = jwt.verify(token, secret) as any
      expect(decoded.role).to.equal('member')
    })

    it('should omit role when role is null', () => {
      const token = authJwtGenerator(secret, 'e@x.com', 'u1', 'o1', 'Name', 'free', null)
      const decoded = jwt.verify(token, secret) as any
      expect(decoded).to.not.have.property('role')
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
