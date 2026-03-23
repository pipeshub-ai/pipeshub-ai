import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOAuthRedirectMiddleware } from '../../../../src/modules/oauth_provider/middlewares/oauth.redirect.middleware'

describe('OAuthRedirectMiddleware', () => {
  let mockTokenService: any
  let mockLogger: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
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
