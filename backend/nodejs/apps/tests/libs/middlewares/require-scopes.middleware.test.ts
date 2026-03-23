import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { requireScopes } from '../../../src/libs/middlewares/require-scopes.middleware'
import { ForbiddenError } from '../../../src/libs/errors/http.errors'
import { OAuthScopeNames } from '../../../src/libs/enums/oauth-scopes.enum'

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

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('requireScopes Middleware', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // No user (not authenticated)
  // -----------------------------------------------------------------------
  describe('No user attached', () => {
    it('should call next with ForbiddenError when req.user is undefined', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_READ)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ForbiddenError)
      expect(error.message).to.equal('Authentication required')
    })

    it('should call next with ForbiddenError when req.user is null', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_READ)
      const req = createMockRequest({ user: null })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ForbiddenError)
    })
  })

  // -----------------------------------------------------------------------
  // Non-OAuth user (regular JWT) - passes through
  // -----------------------------------------------------------------------
  describe('Regular JWT user (non-OAuth)', () => {
    it('should call next() without error for regular JWT users', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_READ)
      const req = createMockRequest({
        user: { userId: 'user1', orgId: 'org1' }, // no isOAuth
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next() without error when isOAuth is false', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_READ)
      const req = createMockRequest({
        user: { userId: 'user1', isOAuth: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // OAuth user - scope checks
  // -----------------------------------------------------------------------
  describe('OAuth user - scope validation', () => {
    it('should call next() when OAuth user has the required scope', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_READ)
      const req = createMockRequest({
        user: {
          userId: 'user1',
          isOAuth: true,
          oauthScopes: ['user:read', 'kb:read'],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next with ForbiddenError when OAuth user lacks required scope', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_WRITE)
      const req = createMockRequest({
        user: {
          userId: 'user1',
          isOAuth: true,
          oauthScopes: ['user:read'],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ForbiddenError)
      expect(error.message).to.include('Insufficient scope')
      expect(error.message).to.include('user:write')
    })

    it('should pass when any one of multiple required scopes is present (OR logic)', () => {
      const middleware = requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.KB_WRITE)
      const req = createMockRequest({
        user: {
          userId: 'user1',
          isOAuth: true,
          oauthScopes: ['kb:write'],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should fail when none of multiple required scopes is present', () => {
      const middleware = requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.KB_WRITE)
      const req = createMockRequest({
        user: {
          userId: 'user1',
          isOAuth: true,
          oauthScopes: ['user:read'],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ForbiddenError)
      expect(error.message).to.include('kb:read or kb:write')
    })

    it('should handle empty oauthScopes array', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_READ)
      const req = createMockRequest({
        user: {
          userId: 'user1',
          isOAuth: true,
          oauthScopes: [],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ForbiddenError)
    })

    it('should handle missing oauthScopes (default to empty array)', () => {
      const middleware = requireScopes(OAuthScopeNames.USER_READ)
      const req = createMockRequest({
        user: {
          userId: 'user1',
          isOAuth: true,
          // no oauthScopes property
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      middleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(ForbiddenError)
    })
  })

  // -----------------------------------------------------------------------
  // Different scope types
  // -----------------------------------------------------------------------
  describe('Various scope types', () => {
    const scopeTestCases = [
      { scope: OAuthScopeNames.ORG_READ, value: 'org:read' },
      { scope: OAuthScopeNames.CONNECTOR_WRITE, value: 'connector:write' },
      { scope: OAuthScopeNames.CONVERSATION_CHAT, value: 'conversation:chat' },
      { scope: OAuthScopeNames.AGENT_EXECUTE, value: 'agent:execute' },
      { scope: OAuthScopeNames.OPENID, value: 'openid' },
    ]

    scopeTestCases.forEach(({ scope, value }) => {
      it(`should accept OAuth user with ${value} scope`, () => {
        const middleware = requireScopes(scope)
        const req = createMockRequest({
          user: {
            userId: 'user1',
            isOAuth: true,
            oauthScopes: [value],
          },
        })
        const res = createMockResponse()
        const next = createMockNext()

        middleware(req, res, next)

        expect(next.calledOnce).to.be.true
        expect(next.firstCall.args).to.have.length(0)
      })
    })
  })
})
