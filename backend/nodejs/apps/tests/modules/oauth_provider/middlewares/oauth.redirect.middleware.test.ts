import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { getCurrentOrgId } from '../../../../src/libs/context/orgContext'
import { RequestContextStore, getRequestContext, runWithRequestContext } from '../../../../src/libs/context/request-context'
import { createOAuthRedirectMiddleware } from '../../../../src/modules/oauth_provider/middlewares/oauth.redirect.middleware'
import * as sessionUtils from '../../../../src/libs/utils/user-session.utils'

describe('OAuthRedirectMiddleware', () => {
  let mockTokenService: any
  let mockLogger: any
  let mockRes: any
  let mockNext: any
  let findSessionRefusal: sinon.SinonStub

  beforeEach(() => {
    findSessionRefusal = sinon.stub(sessionUtils, 'findSessionRefusal').resolves(null)
    mockTokenService = { verifyToken: sinon.stub() }
    mockLogger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    mockRes = {
      redirect: sinon.stub(),
      json: sinon.stub(),
      status: sinon.stub().returnsThis(),
    }
    mockNext = sinon.stub()
  })

  afterEach(() => { sinon.restore() })

  describe('createOAuthRedirectMiddleware', () => {
    it('should redirect to frontend when no auth token', async () => {
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')
      const req = {
        headers: {},
        query: { client_id: 'cid', redirect_uri: 'https://example.com/cb', scope: 'org:read' },
      } as any

      await mw(req, mockRes, mockNext)
      expect(mockRes.redirect.calledOnce).to.be.true
      const redirectUrl = mockRes.redirect.firstCall.args[0]
      expect(redirectUrl).to.include('http://frontend:3000/oauth/authorize')
      expect(redirectUrl).to.include('client_id=cid')
    })

    it('should call next for valid Bearer token', async () => {
      mockTokenService.verifyToken.resolves({
        userId: 'u1', orgId: 'o1', email: 'u@e.com',
        firstName: 'John', lastName: 'Doe', role: 'admin', accountType: 'business',
      })
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')
      const req = {
        headers: { authorization: 'Bearer valid-token' },
        query: {},
      } as any

      await mw(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      expect((req as any).user.userId).to.equal('u1')
    })

    it('should run the handlers after it in the token organisation', async () => {
      mockTokenService.verifyToken.resolves({ userId: 'u1', orgId: 'o1', email: 'u@e.com' })
      let bound: string | undefined
      const next = sinon.stub().callsFake(() => {
        bound = getCurrentOrgId()
      })
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')

      await mw({ headers: { authorization: 'Bearer valid-token' }, query: {} } as any, mockRes, next)

      expect(bound).to.equal('o1')
    })

    it('should put the token org and user on the log context, never the email', async () => {
      mockTokenService.verifyToken.resolves({ userId: 'u1', orgId: 'o1', email: 'u@e.com' })
      let seen: RequestContextStore | undefined
      const next = sinon.stub().callsFake(() => {
        seen = getRequestContext()
      })
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')

      await runWithRequestContext({ rootId: 'r1' }, () =>
        mw({ headers: { authorization: 'Bearer valid-token' }, query: {} } as any, mockRes, next),
      )

      expect(seen).to.deep.equal({ rootId: 'r1', orgId: 'o1', userId: 'u1' })
    })

    it('should redirect when token is expired/invalid', async () => {
      mockTokenService.verifyToken.rejects(new Error('expired'))
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')
      const req = {
        headers: { authorization: 'Bearer expired-token' },
        query: { client_id: 'cid' },
      } as any

      await mw(req, mockRes, mockNext)
      expect(mockRes.redirect.calledOnce).to.be.true
    })

    // A PAT or OAuth token carries no role and fails this the same way a signed-out session does.
    it('should redirect to sign in when the token is not a live session', async () => {
      mockTokenService.verifyToken.resolves({ userId: 'u1', orgId: 'o1', tokenType: 'oauth' })
      findSessionRefusal.resolves('Session expired, please login again')
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')
      const req = { headers: { authorization: 'Bearer pat-jwt' }, query: { client_id: 'cid' } } as any

      await mw(req, mockRes, mockNext)

      expect(findSessionRefusal.firstCall.args[0]).to.include({ userId: 'u1', orgId: 'o1' })
      expect(mockRes.redirect.calledOnce).to.be.true
      expect(mockRes.redirect.firstCall.args[0]).to.include('http://frontend:3000/oauth/authorize?client_id=cid')
      expect(mockNext.called).to.be.false
      expect(req.user).to.equal(undefined)
    })

    // The order is the point: identity is bound after the signature check and before the session check.
    it('binds the verified identity before the session check, so the refusal line says whose session it was', async () => {
      mockTokenService.verifyToken.resolves({ userId: 'u1', orgId: 'o1', email: 'u@e.com' })
      findSessionRefusal.resolves('Session expired, please login again')
      let atRefusal: RequestContextStore | undefined
      mockLogger.info.callsFake((message: string) => {
        if (message.startsWith('Session refused')) atRefusal = { ...getRequestContext()! }
      })
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')

      await runWithRequestContext({ rootId: 'r1' }, () =>
        mw({ headers: { authorization: 'Bearer valid-token' }, query: {} } as any, mockRes, mockNext),
      )

      expect(mockRes.redirect.calledOnce).to.be.true
      expect(atRefusal).to.deep.equal({ rootId: 'r1', orgId: 'o1', userId: 'u1' })
    })

    it('binds nothing for a token that fails verification', async () => {
      mockTokenService.verifyToken.rejects(new Error('invalid signature'))
      let store: RequestContextStore | undefined
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')

      // Readable claims under a wrong signature: nothing may be taken from them before verification passes.
      const forged = jwt.sign({ userId: 'u1', orgId: 'o1' }, 'not-the-secret')
      await runWithRequestContext({ rootId: 'r1' }, async () => {
        await mw({ headers: { authorization: `Bearer ${forged}` }, query: {} } as any, mockRes, mockNext)
        store = getRequestContext()
      })

      expect(mockRes.redirect.calledOnce).to.be.true
      expect(findSessionRefusal.called).to.be.false
      expect(store).to.deep.equal({ rootId: 'r1' })
    })

    it('should pass a failed session check on as an error, not as a sign-in', async () => {
      mockTokenService.verifyToken.resolves({ userId: 'u1', orgId: 'o1', role: 'admin' })
      const failure = new Error('mongo down')
      findSessionRefusal.rejects(failure)
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')

      await mw({ headers: { authorization: 'Bearer valid-token' }, query: {} } as any, mockRes, mockNext)

      expect(mockRes.redirect.called).to.be.false
      expect(mockNext.calledOnceWithExactly(failure)).to.be.true
    })

    it('should call next with error on unexpected failures', async () => {
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')
      // Simulate unexpected error by making req.headers throw
      const req = {
        get headers() { throw new Error('unexpected') },
        query: {},
      } as any

      await mw(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should copy all query parameters to redirect URL', async () => {
      const mw = createOAuthRedirectMiddleware(mockTokenService, mockLogger, 'http://frontend:3000')
      const req = {
        headers: {},
        query: { client_id: 'cid', state: 'xyz', scope: 'org:read' },
      } as any

      await mw(req, mockRes, mockNext)
      const redirectUrl = mockRes.redirect.firstCall.args[0]
      expect(redirectUrl).to.include('state=xyz')
      expect(redirectUrl).to.include('scope=org')
    })
  })
})
