import 'reflect-metadata'
import { expect } from 'chai'
import sinon, { SinonStub } from 'sinon'
import jwt from 'jsonwebtoken'
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { Logger } from '../../../src/libs/services/logger.service'
import { UnauthorizedError } from '../../../src/libs/errors/http.errors'
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema'
import { Users } from '../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../src/modules/user_management/schema/org.schema'
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: {},
    body: {},
    params: {},
    query: {},
    path: '/test',
    method: 'GET',
    ip: '127.0.0.1',
    get: sinon.stub(),
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.send.returns(res)
  res.setHeader.returns(res)
  return res
}

function createMockNext(): SinonStub {
  return sinon.stub()
}

function createMockQuery(resolvedValue: any = null) {
  const query: any = {
    select: sinon.stub(),
    sort: sinon.stub(),
    lean: sinon.stub(),
    exec: sinon.stub().resolves(resolvedValue),
  }
  query.select.returns(query)
  query.sort.returns(query)
  query.lean.returns(query)
  return query
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AuthMiddleware', () => {
  let authMiddleware: AuthMiddleware
  let tokenService: sinon.SinonStubbedInstance<AuthTokenService>
  let logger: sinon.SinonStubbedInstance<Logger>
  let oauthTokenServiceFactory: SinonStub

  beforeEach(() => {
    logger = sinon.createStubInstance(Logger)
    tokenService = sinon.createStubInstance(AuthTokenService)
    oauthTokenServiceFactory = sinon.stub().returns(null)
    authMiddleware = new AuthMiddleware(
      logger as unknown as Logger,
      tokenService as unknown as AuthTokenService,
      oauthTokenServiceFactory,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // extractToken
  // -----------------------------------------------------------------------
  describe('extractToken', () => {
    it('should return null when no authorization header is present', () => {
      const req = createMockRequest()
      const result = authMiddleware.extractToken(req)
      expect(result).to.be.null
    })

    it('should return null when authorization header has wrong scheme', () => {
      const req = createMockRequest({ headers: { authorization: 'Basic abc123' } })
      const result = authMiddleware.extractToken(req)
      expect(result).to.be.null
    })

    it('should return null when Bearer has no token', () => {
      const req = createMockRequest({ headers: { authorization: 'Bearer ' } })
      const result = authMiddleware.extractToken(req)
      expect(result).to.be.null
    })

    it('should return token when valid Bearer token is present', () => {
      const req = createMockRequest({ headers: { authorization: 'Bearer my-token' } })
      const result = authMiddleware.extractToken(req)
      expect(result).to.equal('my-token')
    })

    it('should return null when authorization header is empty', () => {
      const req = createMockRequest({ headers: { authorization: '' } })
      const result = authMiddleware.extractToken(req)
      expect(result).to.be.null
    })
  })

  // -----------------------------------------------------------------------
  // authenticate - no token
  // -----------------------------------------------------------------------
  describe('authenticate - no token', () => {
    it('should call next with UnauthorizedError when no token is provided', async () => {
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.message).to.equal('No token provided')
    })
  })

  // -----------------------------------------------------------------------
  // authenticate - regular token (non-OAuth)
  // -----------------------------------------------------------------------
  describe('authenticate - regular token', () => {
    const validToken = 'regular-token'

    beforeEach(() => {
      sinon.stub(jwt, 'decode').returns({ userId: 'user1', orgId: 'org1' })
      const userQuery = createMockQuery({ _id: 'user1', orgId: 'org1', isDeleted: false })
      sinon.stub(Users, 'findOne').returns(userQuery)
    })

    it('should authenticate and call next() on valid regular token with no logout/password activity', async () => {
      const decoded = { userId: 'user1', orgId: 'org1', iat: Math.floor(Date.now() / 1000) }
      tokenService.verifyToken.resolves(decoded)

      const mockQuery = createMockQuery(null)
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const req = createMockRequest({ headers: { authorization: `Bearer ${validToken}` } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.user).to.deep.equal(decoded)
    })

    it('should strip x-oauth-user-id header to prevent spoofing', async () => {
      const decoded = { userId: 'user1', orgId: 'org1', iat: Math.floor(Date.now() / 1000) }
      tokenService.verifyToken.resolves(decoded)

      const mockQuery = createMockQuery(null)
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const req = createMockRequest({
        headers: {
          authorization: `Bearer ${validToken}`,
          'x-oauth-user-id': 'spoofed-user',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(req.headers['x-oauth-user-id']).to.be.undefined
    })

    it('should reject token if logout activity is newer than token iat', async () => {
      const tokenIat = Math.floor(Date.now() / 1000) - 3600 // 1 hour ago
      const decoded = { userId: 'user1', orgId: 'org1', iat: tokenIat }
      tokenService.verifyToken.resolves(decoded)

      const logoutActivity = {
        activityType: 'LOGOUT',
        createdAt: { getTime: () => Date.now() }, // now (after token + delay)
      }
      const mockQuery = createMockQuery(logoutActivity)
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const req = createMockRequest({ headers: { authorization: `Bearer ${validToken}` } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.message).to.equal('Session expired, please login again')
    })

    it('should allow token if activity timestamp is before token iat + delay', async () => {
      const tokenIat = Math.floor(Date.now() / 1000)
      const decoded = { userId: 'user1', orgId: 'org1', iat: tokenIat }
      tokenService.verifyToken.resolves(decoded)

      // Activity happened before token issuance
      const oldActivity = {
        activityType: 'PASSWORD CHANGED',
        createdAt: { getTime: () => (tokenIat * 1000) - 5000 }, // 5s before token
      }
      const mockQuery = createMockQuery(oldActivity)
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const req = createMockRequest({ headers: { authorization: `Bearer ${validToken}` } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next with error when tokenService.verifyToken throws', async () => {
      tokenService.verifyToken.rejects(new UnauthorizedError('Invalid token'))

      const req = createMockRequest({ headers: { authorization: `Bearer ${validToken}` } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
    })

    it('should still authenticate if UserActivities.findOne throws (non-fatal)', async () => {
      const decoded = { userId: 'user1', orgId: 'org1', iat: Math.floor(Date.now() / 1000) }
      tokenService.verifyToken.resolves(decoded)

      const mockQuery = createMockQuery(null)
      mockQuery.exec.rejects(new Error('DB error'))
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const req = createMockRequest({ headers: { authorization: `Bearer ${validToken}` } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      // Should still proceed since activity check failure is caught
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should skip activity check when userId or orgId are missing', async () => {
      const decoded = { iat: Math.floor(Date.now() / 1000) }
      tokenService.verifyToken.resolves(decoded)

      const findOneStub = sinon.stub(UserActivities, 'findOne')

      const req = createMockRequest({ headers: { authorization: `Bearer ${validToken}` } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(findOneStub.called).to.be.false
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // authenticate - OAuth token
  // -----------------------------------------------------------------------
  describe('authenticate - OAuth token', () => {
    let mockOAuthTokenService: any

    beforeEach(() => {
      mockOAuthTokenService = {
        verifyAccessToken: sinon.stub(),
      }
      oauthTokenServiceFactory.returns(mockOAuthTokenService)
    })

    it('should reject if oauth factory returns null (not configured)', async () => {
      oauthTokenServiceFactory.returns(null)

      sinon.stub(jwt, 'decode').returns({
        tokenType: 'oauth',
        client_id: 'client123',
        iss: 'https://example.com',
      })

      const req = createMockRequest({ headers: { authorization: 'Bearer oauth-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.message).to.equal('OAuth authentication is not configured')
    })

    it('should authenticate an authorization_code OAuth token and set req.user', async () => {
      sinon.stub(jwt, 'decode').returns({
        tokenType: 'oauth',
        client_id: 'client123',
        iss: 'https://example.com',
      })

      mockOAuthTokenService.verifyAccessToken.resolves({
        userId: 'user1',
        orgId: 'org1',
        client_id: 'client123',
        scope: 'user:read kb:read',
        fullName: 'Test User',
        accountType: 'premium',
      })

      const userQuery = createMockQuery({ email: 'test@example.com', fullName: 'Test User' })
      sinon.stub(Users, 'findOne').returns(userQuery)

      const req = createMockRequest({ headers: { authorization: 'Bearer oauth-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.user).to.deep.include({
        userId: 'user1',
        orgId: 'org1',
        email: 'test@example.com',
        fullName: 'Test User',
        isOAuth: true,
        oauthClientId: 'client123',
      })
      expect(req.user.oauthScopes).to.deep.equal(['user:read', 'kb:read'])
    })

    it('should reject deprecated OAuth JWTs where userId equals client_id', async () => {
      sinon.stub(jwt, 'decode').returns({
        tokenType: 'oauth',
        client_id: 'client123',
        iss: 'https://example.com',
      })

      mockOAuthTokenService.verifyAccessToken.resolves({
        userId: 'client123',
        orgId: 'org1',
        client_id: 'client123',
        scope: 'kb:read',
        createdBy: 'real-owner-id',
      })

      const req = createMockRequest({ headers: { authorization: 'Bearer oauth-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.message).to.equal('Invalid or expired token')
    })

    it('should throw UnauthorizedError when Org lookup fails for OAuth token', async () => {
      sinon.stub(jwt, 'decode').returns({
        tokenType: 'oauth',
        client_id: 'client123',
        iss: 'https://example.com',
      })

      mockOAuthTokenService.verifyAccessToken.resolves({
        userId: 'owner-id',
        orgId: 'org1',
        client_id: 'client123',
        scope: 'kb:read',
        // accountType not set
      })

      const userQuery = createMockQuery({ email: 'user@test.com', fullName: 'User' })
      sinon.stub(Users, 'findOne').returns(userQuery)

      const orgQuery = createMockQuery(null)
      orgQuery.exec.rejects(new Error('Org DB failure'))
      sinon.stub(Org, 'findOne').returns(orgQuery)

      const req = createMockRequest({ headers: { authorization: 'Bearer oauth-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.message).to.equal('Failed to look up org for OAuth token')
    })

    it('should use fullName from user DB when not in payload', async () => {
      sinon.stub(jwt, 'decode').returns({
        tokenType: 'oauth',
        client_id: 'client123',
        iss: 'https://example.com',
      })

      mockOAuthTokenService.verifyAccessToken.resolves({
        userId: 'user1',
        orgId: 'org1',
        client_id: 'client123',
        scope: 'user:read',
        accountType: 'basic',
        // no fullName in payload
      })

      const userQuery = createMockQuery({ email: 'user@test.com', fullName: 'DB Name' })
      sinon.stub(Users, 'findOne').returns(userQuery)

      const req = createMockRequest({ headers: { authorization: 'Bearer oauth-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(req.user.fullName).to.equal('DB Name')
    })

    it('should handle Users.findOne failure during OAuth user email lookup', async () => {
      sinon.stub(jwt, 'decode').returns({
        tokenType: 'oauth',
        client_id: 'client123',
        iss: 'https://example.com',
      })

      mockOAuthTokenService.verifyAccessToken.resolves({
        userId: 'user1',
        orgId: 'org1',
        client_id: 'client123',
        scope: 'user:read',
        accountType: 'basic',
        fullName: 'Payload Name',
      })

      // Make Users.findOne throw an error
      const userQuery = createMockQuery(null)
      userQuery.exec.rejects(new Error('Users DB error'))
      sinon.stub(Users, 'findOne').returns(userQuery)

      const req = createMockRequest({ headers: { authorization: 'Bearer oauth-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      // Should still authenticate successfully (error is caught and logged)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      // Logger should have been called with the error
      expect(logger.error.calledWithMatch('Failed to look up OAuth user email')).to.be.true
    })

    it('should not set x-oauth-user-id header for authorization_code flow', async () => {
      sinon.stub(jwt, 'decode').returns({
        tokenType: 'oauth',
        client_id: 'client123',
        iss: 'https://example.com',
      })

      mockOAuthTokenService.verifyAccessToken.resolves({
        userId: 'user1', // not equal to client_id -> authorization_code
        orgId: 'org1',
        client_id: 'client123',
        scope: 'user:read',
        fullName: 'User',
        accountType: 'basic',
      })

      const userQuery = createMockQuery({ email: 'user@test.com', fullName: 'User' })
      sinon.stub(Users, 'findOne').returns(userQuery)

      const req = createMockRequest({ headers: { authorization: 'Bearer oauth-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await authMiddleware.authenticate(req, res, next)

      expect(req.headers['x-oauth-user-id']).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // scopedTokenValidator
  // -----------------------------------------------------------------------
  describe('scopedTokenValidator', () => {
    it('should call next() on valid scoped token', async () => {
      const decoded = { userId: 'user1', orgId: 'org1', iat: Math.floor(Date.now() / 1000), scopes: ['mail:send'] }
      tokenService.verifyScopedToken.resolves(decoded)

      const middleware = authMiddleware.scopedTokenValidator('mail:send')
      const req = createMockRequest({ headers: { authorization: 'Bearer scoped-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.tokenPayload).to.deep.equal(decoded)
    })

    it('should call next with error when no token provided', async () => {
      const middleware = authMiddleware.scopedTokenValidator('mail:send')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.message).to.equal('No token provided')
    })

    it('should call next with error when scoped token verification fails', async () => {
      tokenService.verifyScopedToken.rejects(new UnauthorizedError('Invalid token'))

      const middleware = authMiddleware.scopedTokenValidator('mail:send')
      const req = createMockRequest({ headers: { authorization: 'Bearer bad-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
    })

    it('should reject password reset scoped token if password was changed after token iat', async () => {
      const tokenIat = Math.floor(Date.now() / 1000) - 3600 // 1 hour ago
      const decoded = {
        userId: 'user1',
        orgId: 'org1',
        iat: tokenIat,
        scopes: [TokenScopes.PASSWORD_RESET],
      }
      tokenService.verifyScopedToken.resolves(decoded)

      const activity = {
        activityType: 'PASSWORD CHANGED',
        createdAt: { getTime: () => Date.now() }, // now - after token
      }
      const mockQuery = createMockQuery(activity)
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const middleware = authMiddleware.scopedTokenValidator(TokenScopes.PASSWORD_RESET)
      const req = createMockRequest({ headers: { authorization: 'Bearer scoped-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.message).to.include('Password reset link expired')
    })

    it('should allow password reset token if no recent password change activity', async () => {
      const decoded = {
        userId: 'user1',
        orgId: 'org1',
        iat: Math.floor(Date.now() / 1000),
        scopes: [TokenScopes.PASSWORD_RESET],
      }
      tokenService.verifyScopedToken.resolves(decoded)

      const mockQuery = createMockQuery(null) // no activity
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const middleware = authMiddleware.scopedTokenValidator(TokenScopes.PASSWORD_RESET)
      const req = createMockRequest({ headers: { authorization: 'Bearer scoped-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should not check password activity for non-password-reset scopes', async () => {
      const decoded = {
        userId: 'user1',
        orgId: 'org1',
        iat: Math.floor(Date.now() / 1000),
        scopes: ['mail:send'],
      }
      tokenService.verifyScopedToken.resolves(decoded)

      const findOneStub = sinon.stub(UserActivities, 'findOne')

      const middleware = authMiddleware.scopedTokenValidator('mail:send')
      const req = createMockRequest({ headers: { authorization: 'Bearer scoped-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      expect(findOneStub.called).to.be.false
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should still authenticate if UserActivities lookup throws for PASSWORD_RESET scope', async () => {
      const decoded = {
        userId: 'user1',
        orgId: 'org1',
        iat: Math.floor(Date.now() / 1000),
        scopes: [TokenScopes.PASSWORD_RESET],
      }
      tokenService.verifyScopedToken.resolves(decoded)

      const mockQuery = createMockQuery(null)
      mockQuery.exec.rejects(new Error('DB error'))
      sinon.stub(UserActivities, 'findOne').returns(mockQuery)

      const middleware = authMiddleware.scopedTokenValidator(TokenScopes.PASSWORD_RESET)
      const req = createMockRequest({ headers: { authorization: 'Bearer scoped-token' } })
      const res = createMockResponse()
      const next = createMockNext()

      await middleware(req, res, next)

      // Non-fatal: should still call next without error
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })
})
