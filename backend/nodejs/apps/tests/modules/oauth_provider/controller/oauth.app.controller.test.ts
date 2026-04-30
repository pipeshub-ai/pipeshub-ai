import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OAuthAppController } from '../../../../src/modules/oauth_provider/controller/oauth.app.controller'
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service'

describe('OAuthAppController', () => {
  let controller: OAuthAppController
  let mockLogger: any
  let mockOAuthAppService: any
  let mockOAuthTokenService: any
  let mockScopeValidatorService: any
  let mockReq: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(true)
    mockLogger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    mockOAuthAppService = {
      listApps: sinon.stub(),
      createApp: sinon.stub(),
      getAppById: sinon.stub(),
      updateApp: sinon.stub(),
      deleteApp: sinon.stub(),
      regenerateSecret: sinon.stub(),
      suspendApp: sinon.stub(),
      activateApp: sinon.stub(),
    }
    mockOAuthTokenService = {
      revokeAllTokensForApp: sinon.stub().resolves(),
      listTokensForApp: sinon.stub().resolves([]),
    }
    mockScopeValidatorService = {
      getScopesGroupedByCategory: sinon.stub().returns({}),
      getScopesGroupedByCategoryForRole: sinon.stub().returns({}),
    }
    controller = new OAuthAppController(
      mockLogger,
      mockOAuthAppService,
      mockOAuthTokenService,
      mockScopeValidatorService,
    )
    mockReq = { user: { orgId: 'org-1', userId: 'user-1' }, query: {}, params: {}, body: {} }
    mockRes = { json: sinon.stub(), status: sinon.stub().returnsThis() }
    mockNext = sinon.stub()
  })

  afterEach(() => { sinon.restore() })

  describe('listApps', () => {
    it('should return apps list', async () => {
      const apps = { data: [], pagination: { page: 1, limit: 20, total: 0, totalPages: 0 } }
      mockOAuthAppService.listApps.resolves(apps)
      await controller.listApps(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledWith(apps)).to.be.true
    })

    it('should parse page and limit from query', async () => {
      mockReq.query = { page: '2', limit: '10', status: 'active', search: 'test' }
      mockOAuthAppService.listApps.resolves({ data: [], pagination: {} })
      await controller.listApps(mockReq, mockRes, mockNext)
      const callArgs = mockOAuthAppService.listApps.firstCall.args
      const query = callArgs[3] as { page?: number; limit?: number }
      expect(query.page).to.equal(2)
      expect(query.limit).to.equal(10)
    })

    it('should call next on error', async () => {
      mockOAuthAppService.listApps.rejects(new Error('fail'))
      await controller.listApps(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('createApp', () => {
    it('should return 201 with created app', async () => {
      const app = { id: 'app-1', clientId: 'cid', clientSecret: 'secret' }
      mockOAuthAppService.createApp.resolves(app)
      mockReq.body = { name: 'Test', allowedScopes: ['org:read'] }
      await controller.createApp(mockReq, mockRes, mockNext)
      expect(mockRes.status.calledWith(201)).to.be.true
      expect(mockRes.json.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      mockOAuthAppService.createApp.rejects(new Error('fail'))
      await controller.createApp(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('getApp', () => {
    it('should return app', async () => {
      const app = { id: 'app-1', name: 'Test' }
      mockOAuthAppService.getAppById.resolves(app)
      mockReq.params = { appId: 'app-1' }
      await controller.getApp(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledWith(app)).to.be.true
    })

    it('should call next on error', async () => {
      mockOAuthAppService.getAppById.rejects(new Error('not found'))
      mockReq.params = { appId: 'app-1' }
      await controller.getApp(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('updateApp', () => {
    it('should return updated app', async () => {
      const app = { id: 'app-1', name: 'Updated' }
      mockOAuthAppService.updateApp.resolves(app)
      mockReq.params = { appId: 'app-1' }
      mockReq.body = { name: 'Updated' }
      await controller.updateApp(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })
  })

  describe('deleteApp', () => {
    it('should delete app and revoke tokens', async () => {
      mockOAuthAppService.getAppById.resolves({ clientId: 'cid' })
      mockOAuthAppService.deleteApp.resolves()
      mockReq.params = { appId: 'app-1' }
      await controller.deleteApp(mockReq, mockRes, mockNext)
      expect(mockOAuthTokenService.revokeAllTokensForApp.calledWith('cid')).to.be.true
      expect(mockRes.json.calledOnce).to.be.true
    })
  })

  describe('regenerateSecret', () => {
    it('should return new secret', async () => {
      mockOAuthAppService.regenerateSecret.resolves({ clientId: 'cid', clientSecret: 'new-secret' })
      mockReq.params = { appId: 'app-1' }
      await controller.regenerateSecret(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })
  })

  describe('suspendApp', () => {
    it('should suspend app', async () => {
      mockOAuthAppService.suspendApp.resolves({ id: 'app-1', status: 'suspended' })
      mockReq.params = { appId: 'app-1' }
      await controller.suspendApp(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })
  })

  describe('activateApp', () => {
    it('should activate app', async () => {
      mockOAuthAppService.activateApp.resolves({ id: 'app-1', status: 'active' })
      mockReq.params = { appId: 'app-1' }
      await controller.activateApp(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })
  })

  describe('listScopes', () => {
    it('should return scopes grouped by category', async () => {
      mockScopeValidatorService.getScopesGroupedByCategory.returns({ Organization: [] })
      await controller.listScopes(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })
  })

  describe('listAppTokens', () => {
    it('should return tokens for an app', async () => {
      mockOAuthAppService.getAppById.resolves({ clientId: 'cid' })
      mockOAuthTokenService.listTokensForApp.resolves([])
      mockReq.params = { appId: 'app-1' }
      await controller.listAppTokens(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })
  })

  describe('revokeAllTokens', () => {
    it('should revoke all tokens for app', async () => {
      mockOAuthAppService.getAppById.resolves({ clientId: 'cid' })
      mockReq.params = { appId: 'app-1' }
      await controller.revokeAllTokens(mockReq, mockRes, mockNext)
      expect(mockOAuthTokenService.revokeAllTokensForApp.calledWith('cid')).to.be.true
      expect(mockRes.json.calledOnce).to.be.true
    })
  })
})
