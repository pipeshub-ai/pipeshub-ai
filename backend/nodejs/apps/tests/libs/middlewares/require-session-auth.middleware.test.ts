import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { requireSessionAuth } from '../../../src/libs/middlewares/require-session-auth.middleware'
import { ForbiddenError, UnauthorizedError } from '../../../src/libs/errors/http.errors'

function createMockRequest(user?: Record<string, any>): any {
  return { headers: {}, body: {}, params: {}, query: {}, user }
}

describe('requireSessionAuth Middleware', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should call next with UnauthorizedError when req.user is undefined', () => {
    const next = sinon.stub()
    requireSessionAuth(createMockRequest(), {} as any, next)
    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
  })

  it('should pass through a session JWT user (no isOAuth flag)', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({ userId: 'u1', orgId: 'o1', role: 'member' }),
      {} as any,
      next,
    )
    expect(next.calledOnceWithExactly()).to.be.true
  })

  it('should pass through when isOAuth is explicitly false', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({ userId: 'u1', orgId: 'o1', isOAuth: false }),
      {} as any,
      next,
    )
    expect(next.calledOnceWithExactly()).to.be.true
  })

  it('should reject an OAuth access token user with ForbiddenError (403)', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({
        userId: 'u1',
        orgId: 'o1',
        role: 'admin',
        isOAuth: true,
        oauthClientId: 'client-1',
        oauthScopes: ['org:admin'],
      }),
      {} as any,
      next,
    )
    expect(next.calledOnce).to.be.true
    const err = next.firstCall.args[0]
    expect(err).to.be.instanceOf(ForbiddenError)
    expect(err.statusCode).to.equal(403)
  })

  it('should reject a personal access token user (PATs authenticate as OAuth tokens)', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({
        userId: 'u1',
        orgId: 'o1',
        isOAuth: true,
        oauthClientId: 'pat-system:o1',
        oauthScopes: ['org:read'],
      }),
      {} as any,
      next,
    )
    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })
})
