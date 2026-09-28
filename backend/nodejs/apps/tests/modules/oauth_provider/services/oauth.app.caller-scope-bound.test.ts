import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service'
import { OAuthAppController } from '../../../../src/modules/oauth_provider/controller/oauth.app.controller'
import { ScopeValidatorService } from '../../../../src/modules/oauth_provider/services/scope.validator.service'
import {
  OAuthApp,
  OAuthAppStatus,
  OAuthGrantType,
} from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { ForbiddenError } from '../../../../src/libs/errors/http.errors'
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service'
import { createMockLogger } from '../../../helpers/mock-logger'

// T21: an OAuth app is a credential factory (client_credentials tokens act as
// the app's creator with the app's scopes), so an OAuth/PAT caller may only
// create or re-arm one within the scopes its own token holds.
describe('OAuth app management bounded by the calling token (T21)', () => {
  let service: OAuthAppService
  let mockEncryptionService: any

  const orgId = new Types.ObjectId().toString()
  const userId = new Types.ObjectId().toString()
  const appId = new Types.ObjectId().toString()

  function storedApp(allowedScopes: string[], status = OAuthAppStatus.ACTIVE) {
    return {
      _id: new Types.ObjectId(appId),
      clientId: 'client-1',
      name: 'App',
      redirectUris: [],
      allowedGrantTypes: [OAuthGrantType.CLIENT_CREDENTIALS],
      allowedScopes,
      status,
      isConfidential: true,
      accessTokenLifetime: 3600,
      refreshTokenLifetime: 2592000,
      createdAt: new Date(),
      updatedAt: new Date(),
      save: sinon.stub().resolvesThis(),
    }
  }

  beforeEach(() => {
    mockEncryptionService = { encrypt: sinon.stub().returns('enc'), decrypt: sinon.stub() }
    service = new OAuthAppService(
      createMockLogger() as any,
      mockEncryptionService,
      new ScopeValidatorService(),
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('createApp', () => {
    it('refuses scopes the calling token does not hold', async () => {
      const create = sinon.stub(OAuthApp, 'create')

      try {
        await service.createApp(
          orgId,
          userId,
          false,
          {
            name: 'escalate',
            allowedScopes: ['kb:read', 'kb:write'],
            allowedGrantTypes: [OAuthGrantType.CLIENT_CREDENTIALS],
          },
          ['kb:read'],
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ForbiddenError)
        expect(error.message).to.include('kb:write')
      }
      expect(create.called).to.be.false
    })

    it('creates an app whose scopes are a subset of the calling token', async () => {
      const create = sinon.stub(OAuthApp, 'create').resolves(storedApp(['kb:read']) as any)

      await service.createApp(
        orgId,
        userId,
        false,
        { name: 'ok', allowedScopes: ['kb:read'] },
        ['kb:read', 'conversation:chat'],
      )

      expect(create.calledOnce).to.be.true
    })

    it('a session caller keeps today\'s role-based check only', async () => {
      const create = sinon.stub(OAuthApp, 'create').resolves(storedApp(['kb:read', 'kb:write']) as any)

      await service.createApp(orgId, userId, false, {
        name: 'session',
        allowedScopes: ['kb:read', 'kb:write'],
      })

      expect(create.calledOnce).to.be.true
    })
  })

  describe('updateApp', () => {
    it('refuses to widen an app past the calling token', async () => {
      const app = storedApp(['kb:read'])
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)

      try {
        await service.updateApp(
          appId,
          orgId,
          userId,
          false,
          { allowedScopes: ['kb:read', 'kb:delete'] },
          ['kb:read'],
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ForbiddenError)
      }
      expect(app.save.called).to.be.false
    })

    it('refuses to re-point a broader app (e.g. add a grant type) from a narrower token', async () => {
      const app = storedApp(['kb:read', 'kb:write'])
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)

      try {
        await service.updateApp(
          appId,
          orgId,
          userId,
          false,
          { allowedGrantTypes: [OAuthGrantType.CLIENT_CREDENTIALS] },
          ['kb:read'],
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ForbiddenError)
      }
      expect(app.save.called).to.be.false
    })

    it('narrowing a broader app down to the calling token\'s scopes is allowed', async () => {
      const app = storedApp(['kb:read', 'kb:write'])
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)

      await service.updateApp(
        appId,
        orgId,
        userId,
        false,
        { allowedScopes: ['kb:read'] },
        ['kb:read'],
      )

      expect(app.save.calledOnce).to.be.true
      expect(app.allowedScopes).to.deep.equal(['kb:read'])
    })

    it('a session caller may still update a broad app', async () => {
      const app = storedApp(['kb:read', 'kb:write'])
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)

      await service.updateApp(appId, orgId, userId, false, { name: 'renamed' })

      expect(app.save.calledOnce).to.be.true
    })
  })

  describe('regenerateSecret', () => {
    it('refuses to hand a narrower token the secret of a broader app', async () => {
      const app = storedApp(['kb:read', 'agent:execute'])
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)

      try {
        await service.regenerateSecret(appId, orgId, userId, ['kb:read'])
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ForbiddenError)
        expect(error.message).to.include('agent:execute')
      }
      expect(app.save.called).to.be.false
      expect(mockEncryptionService.encrypt.called).to.be.false
    })

    it('regenerates the secret of an app within the calling token', async () => {
      const app = storedApp(['kb:read'])
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)

      const result = await service.regenerateSecret(appId, orgId, userId, ['kb:read', 'semantic:write'])

      expect(result.clientSecret).to.be.a('string')
      expect(app.save.calledOnce).to.be.true
    })
  })

  describe('activateApp', () => {
    it('refuses to re-enable a broader app from a narrower token', async () => {
      const app = storedApp(['kb:read', 'kb:write'], OAuthAppStatus.SUSPENDED)
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)

      try {
        await service.activateApp(appId, orgId, userId, ['kb:read'])
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ForbiddenError)
      }
      expect(app.status).to.equal(OAuthAppStatus.SUSPENDED)
      expect(app.save.called).to.be.false
    })
  })

  describe('OAuthAppController passes the calling token\'s scopes', () => {
    let controller: OAuthAppController
    let appService: any
    let res: any
    let next: sinon.SinonStub

    beforeEach(() => {
      sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(false)
      appService = {
        createApp: sinon.stub().resolves({ id: 'a' }),
        updateApp: sinon.stub().resolves({ id: 'a' }),
        regenerateSecret: sinon.stub().resolves({ clientId: 'c', clientSecret: 's' }),
        activateApp: sinon.stub().resolves({ id: 'a' }),
      }
      controller = new OAuthAppController(
        createMockLogger() as any,
        appService,
        {} as any,
        new ScopeValidatorService(),
      )
      res = { json: sinon.stub(), status: sinon.stub().returnsThis() }
      next = sinon.stub()
    })

    const oauthReq = (extra: Record<string, unknown> = {}) => ({
      user: { orgId, userId, isOAuth: true, oauthScopes: ['kb:read'] },
      params: { appId },
      query: {},
      body: { name: 'x', allowedScopes: ['kb:read'] },
      ...extra,
    })

    it('forwards the scopes of an OAuth/PAT caller on create, update, regenerate and activate', async () => {
      await controller.createApp(oauthReq() as any, res, next)
      await controller.updateApp(oauthReq() as any, res, next)
      await controller.regenerateSecret(oauthReq() as any, res, next)
      await controller.activateApp(oauthReq() as any, res, next)

      expect(appService.createApp.firstCall.args[4]).to.deep.equal(['kb:read'])
      expect(appService.updateApp.firstCall.args[5]).to.deep.equal(['kb:read'])
      expect(appService.regenerateSecret.firstCall.args[3]).to.deep.equal(['kb:read'])
      expect(appService.activateApp.firstCall.args[3]).to.deep.equal(['kb:read'])
      expect(next.called).to.be.false
    })

    it('forwards nothing for a session caller', async () => {
      const sessionReq = oauthReq({ user: { orgId, userId, role: 'member' } })
      await controller.createApp(sessionReq as any, res, next)
      await controller.regenerateSecret(sessionReq as any, res, next)

      expect(appService.createApp.firstCall.args[4]).to.be.undefined
      expect(appService.regenerateSecret.firstCall.args[3]).to.be.undefined
    })
  })
})
