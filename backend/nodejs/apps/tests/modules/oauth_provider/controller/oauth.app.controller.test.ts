import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OAuthAppController } from '../../../../src/modules/oauth_provider/controller/oauth.app.controller'
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service'
import { captureLogRecords, inRequestContext, newRequestId, recordsOf } from '../../../helpers/log-capture'
import { createMockLogger } from '../../../helpers/mock-logger'
import { Types } from 'mongoose'
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { OAuthApp, OAuthAppStatus } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema'
import { OAuthRefreshToken } from '../../../../src/modules/oauth_provider/schema/oauth.refresh_token.schema'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'

describe('OAuthAppController', () => {
  let controller: OAuthAppController
  let mockOAuthAppService: any
  let mockOAuthTokenService: any
  let mockScopeValidatorService: any
  let mockReq: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(true)
    mockOAuthAppService = {
      listApps: sinon.stub(),
      createApp: sinon.stub(),
      getAppById: sinon.stub(),
      updateApp: sinon.stub(),
      deleteApp: sinon.stub(),
      regenerateSecret: sinon.stub(),
      suspendApp: sinon.stub(),
      activateApp: sinon.stub(),
      setTokenIdentity: sinon.stub(),
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
      const query = callArgs[2] as { page?: number; limit?: number }
      expect(query.page).to.equal(2)
      expect(query.limit).to.equal(10)
    })

    it('should pass orgId, userId, query and whether the caller is an org admin', async () => {
      mockOAuthAppService.listApps.resolves({ data: [], pagination: {} })
      mockReq.query = { createdBy: '507f1f77bcf86cd799439013' }
      await controller.listApps(mockReq, mockRes, mockNext)
      const args = mockOAuthAppService.listApps.firstCall.args
      expect(args[0]).to.equal('org-1')
      expect(args[1]).to.equal('user-1')
      expect(args[2].createdBy).to.equal('507f1f77bcf86cd799439013')
      expect(args[3]).to.equal(true)
    })

    it('lists only the caller\'s own apps for a member', async () => {
      ;(userAdminService.isUserOrgAdmin as sinon.SinonStub).resolves(false)
      mockOAuthAppService.listApps.resolves({ data: [], pagination: {} })
      await controller.listApps(mockReq, mockRes, mockNext)
      expect(mockOAuthAppService.listApps.firstCall.args[3]).to.equal(false)
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

    it('should call createApp with isAdmin false when user is not org admin', async () => {
      ;(userAdminService.isUserOrgAdmin as sinon.SinonStub).resolves(false)
      mockOAuthAppService.createApp.resolves({ id: 'a', clientId: 'c', clientSecret: 's' })
      mockReq.body = { name: 'Test', allowedScopes: ['org:read'] }
      await controller.createApp(mockReq, mockRes, mockNext)
      expect(mockOAuthAppService.createApp.firstCall.args[2]).to.equal(false)
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

  describe('setTokenIdentity', () => {
    /**
     * The shape, not just the value. The manage panel reads the binding out of
     * `app` on this body to decide whether the application is still unbound,
     * and the service-level test only covers what `setTokenIdentity` returns —
     * which is the app on its own, so it cannot see this envelope at all.
     */
    it('answers with the app nested under `app`, carrying the binding', async () => {
      const app = {
        id: 'app-1',
        name: 'Nightly export',
        tokenIdentityUserId: 'svc-1',
      }
      mockOAuthAppService.setTokenIdentity.resolves(app)
      mockReq.params = { appId: 'app-1' }
      mockReq.body = { serviceAccountId: 'svc-1' }

      await controller.setTokenIdentity(mockReq, mockRes, mockNext)

      expect(mockRes.json.calledOnce).to.be.true
      const body = mockRes.json.firstCall.args[0]
      expect(body).to.have.property('message')
      expect(body.app).to.deep.equal(app)
      expect(body.app.tokenIdentityUserId).to.equal('svc-1')
    })

    it('stays creator-only: no admin override reaches the service', async () => {
      mockOAuthAppService.setTokenIdentity.resolves({})
      mockReq.params = { appId: 'app-1' }
      mockReq.body = { serviceAccountId: 'svc-1' }
      await controller.setTokenIdentity(mockReq, mockRes, mockNext)
      expect(mockOAuthAppService.setTokenIdentity.firstCall.args).to.deep.equal(['app-1', 'org-1', 'user-1', 'svc-1'])
    })
  })

  describe('deleteApp', () => {
    // OAuthAppService.deleteApp revokes the tokens once the app can no longer mint any; a revoke here would be a second one.
    it('leaves revoking the app\'s tokens to the service', async () => {
      mockOAuthAppService.deleteApp.resolves()
      mockReq.params = { appId: 'app-1' }
      await controller.deleteApp(mockReq, mockRes, mockNext)
      expect(mockOAuthAppService.deleteApp.calledOnce).to.be.true
      expect(mockOAuthTokenService.revokeAllTokensForApp.called).to.be.false
      expect(mockOAuthAppService.getAppById.called).to.be.false
      expect(mockRes.json.calledOnce).to.be.true
    })

    it('lets an org admin delete any app of the org', async () => {
      mockOAuthAppService.deleteApp.resolves()
      mockReq.params = { appId: 'app-1' }
      await controller.deleteApp(mockReq, mockRes, mockNext)
      expect(mockOAuthAppService.deleteApp.firstCall.args).to.deep.equal(['app-1', 'org-1', 'user-1', true])
    })

    it('passes an app it may not manage to the error handler, revoking nothing', async () => {
      const notFound = new NotFoundError('OAuth app not found')
      mockOAuthAppService.deleteApp.rejects(notFound)
      mockReq.params = { appId: 'app-1' }
      await controller.deleteApp(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnceWithExactly(notFound)).to.be.true
      expect(mockOAuthTokenService.revokeAllTokensForApp.called).to.be.false
      expect(mockRes.json.called).to.be.false
    })

    // Real controller, app service and token service; only the Mongo models are stubbed.
    describe('through the real services', () => {
      let app: any
      let statusAtRevoke: string[]
      let tokenLogger: ReturnType<typeof createMockLogger>
      let wired: OAuthAppController
      let req: any

      beforeEach(() => {
        ;(userAdminService.isUserOrgAdmin as sinon.SinonStub).resolves(false)
        const orgId = new Types.ObjectId().toString()
        const userId = new Types.ObjectId().toString()
        const appId = new Types.ObjectId().toString()
        app = {
          _id: new Types.ObjectId(appId), clientId: 'cid', createdBy: new Types.ObjectId(userId),
          isDeleted: false, status: OAuthAppStatus.ACTIVE, save: sinon.stub().resolves(),
        }
        sinon.stub(OAuthApp, 'findOne').resolves(app)
        statusAtRevoke = []
        sinon.stub(OAuthRefreshToken, 'updateMany').resolves({} as any)
        tokenLogger = createMockLogger()
        const tokens = new OAuthTokenService(tokenLogger as any, { secret: 'x'.repeat(40) } as any, 'http://issuer')
        wired = new OAuthAppController(new OAuthAppService(createMockLogger() as any, {} as any, {} as any, tokens), tokens, {} as any)
        req = { user: { orgId, userId }, params: { appId } }
      })

      it('revokes the tokens once, after the app stops minting new ones', async () => {
        sinon.stub(OAuthAccessToken, 'updateMany').callsFake((() => {
          statusAtRevoke.push(app.status)
          return Promise.resolve({})
        }) as any)

        await wired.deleteApp(req, mockRes, mockNext)

        expect(mockNext.called).to.be.false
        expect(mockRes.json.calledOnce).to.be.true
        expect(statusAtRevoke).to.deep.equal([OAuthAppStatus.REVOKED])
        expect(tokenLogger.info.args.filter(([message]) => message === 'All tokens revoked for app')).to.have.length(1)
        expect(app.isDeleted).to.be.true
      })

      it('leaves the app as it was when its tokens cannot be revoked', async () => {
        const down = new Error('token store down')
        sinon.stub(OAuthAccessToken, 'updateMany').rejects(down)

        await wired.deleteApp(req, mockRes, mockNext)

        expect(mockNext.calledOnceWithExactly(down)).to.be.true
        expect(mockRes.json.called).to.be.false
        expect(app.status).to.equal(OAuthAppStatus.ACTIVE)
        expect(app.isDeleted).to.be.false
      })
    })
  })

  describe('regenerateSecret', () => {
    it('should return new secret', async () => {
      mockOAuthAppService.regenerateSecret.resolves({ clientId: 'cid', clientSecret: 'new-secret' })
      mockReq.params = { appId: 'app-1' }
      await controller.regenerateSecret(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })

    it('stays creator-only, even for an org admin', async () => {
      mockOAuthAppService.regenerateSecret.resolves({ clientId: 'cid', clientSecret: 's' })
      mockReq.params = { appId: 'app-1' }
      await controller.regenerateSecret(mockReq, mockRes, mockNext)
      expect(mockOAuthAppService.regenerateSecret.firstCall.args).to.deep.equal(['app-1', 'org-1', 'user-1'])
    })
  })

  describe('suspendApp', () => {
    it('should suspend app', async () => {
      mockOAuthAppService.suspendApp.resolves({ id: 'app-1', status: 'suspended' })
      mockReq.params = { appId: 'app-1' }
      await controller.suspendApp(mockReq, mockRes, mockNext)
      expect(mockRes.json.calledOnce).to.be.true
    })

    it('passes whether the caller is an org admin', async () => {
      ;(userAdminService.isUserOrgAdmin as sinon.SinonStub).resolves(false)
      mockOAuthAppService.suspendApp.resolves({ id: 'app-1', status: 'suspended' })
      mockReq.params = { appId: 'app-1' }
      await controller.suspendApp(mockReq, mockRes, mockNext)
      expect(mockOAuthAppService.suspendApp.firstCall.args).to.deep.equal(['app-1', 'org-1', 'user-1', false])
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
    it('should return scopes grouped by category for org admin', async () => {
      mockScopeValidatorService.getScopesGroupedByCategoryForRole.returns({ Organization: [] })
      await controller.listScopes(mockReq, mockRes, mockNext)
      expect(mockScopeValidatorService.getScopesGroupedByCategoryForRole.calledWith(true)).to.be.true
      expect(mockRes.json.calledOnce).to.be.true
      const payload = mockRes.json.firstCall.args[0] as { scopes: unknown }
      expect(payload.scopes).to.deep.equal({ Organization: [] })
    })

    it('should use member scope grouping when user is not org admin', async () => {
      ;(userAdminService.isUserOrgAdmin as sinon.SinonStub).resolves(false)
      mockScopeValidatorService.getScopesGroupedByCategoryForRole.returns({ Organization: [] })
      await controller.listScopes(mockReq, mockRes, mockNext)
      expect(mockScopeValidatorService.getScopesGroupedByCategoryForRole.calledWith(false)).to.be.true
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

  // The service that makes the write logs the event (OAuthAppService, OAuthTokenService); the controller adds no twin.
  describe('a change that succeeds', () => {
    const changes: Array<[string, (c: OAuthAppController) => Promise<void>]> = [
      ['createApp', (c) => c.createApp(mockReq, mockRes, mockNext)],
      ['setTokenIdentity', (c) => c.setTokenIdentity(mockReq, mockRes, mockNext)],
      ['updateApp', (c) => c.updateApp(mockReq, mockRes, mockNext)],
      ['deleteApp', (c) => c.deleteApp(mockReq, mockRes, mockNext)],
      ['regenerateSecret', (c) => c.regenerateSecret(mockReq, mockRes, mockNext)],
      ['suspendApp', (c) => c.suspendApp(mockReq, mockRes, mockNext)],
      ['activateApp', (c) => c.activateApp(mockReq, mockRes, mockNext)],
      ['revokeAllTokens', (c) => c.revokeAllTokens(mockReq, mockRes, mockNext)],
    ]
    for (const [name, run] of changes) {
      it(`${name} writes no log line of its own`, async () => {
        for (const stub of Object.values(mockOAuthAppService) as sinon.SinonStub[]) {
          stub.resolves({ id: 'app-1', clientId: 'cid', clientSecret: 's' })
        }
        mockReq.params = { appId: 'app-1' }
        mockReq.body = { name: 'n5a7-app-label', serviceAccountId: 'svc-1' }
        const records = captureLogRecords()
        const id = newRequestId('oauth-app')

        await inRequestContext(id, () => run(controller))

        expect(mockNext.called).to.be.false
        expect(mockRes.json.calledOnce).to.be.true
        expect(recordsOf(records, id)).to.deep.equal([])
      })
    }
  })
})
