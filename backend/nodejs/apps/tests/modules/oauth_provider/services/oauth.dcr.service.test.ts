import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { OAuthDcrService } from '../../../../src/modules/oauth_provider/services/oauth.dcr.service'
import { OAuthGrantType } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { BadRequestError, ForbiddenError } from '../../../../src/libs/errors/http.errors'
import { setUserRepository } from '../../../../src/libs/repositories/user'
import { setIdentityPort } from '../../../../src/libs/repositories/identity'
import { getCurrentOrgId } from '../../../../src/libs/context/orgContext'
import { createMockLogger } from '../../../helpers/mock-logger'
import { setOAuthAppRepository } from '../../../../src/libs/repositories/oauth-app'
import { setClientMetadataGetter } from '../../../../src/modules/oauth_provider/services/client_metadata_document'
import { InvalidClientError } from '../../../../src/libs/errors/oauth.errors'

describe('OAuthDcrService', () => {
  let service: OAuthDcrService
  let mockOAuthAppService: any
  let mockScopeValidatorService: any
  const orgId = new Types.ObjectId()
  const userId = new Types.ObjectId()

  beforeEach(() => {
    delete process.env.PIPESHUB_ENABLE_DCR
    mockOAuthAppService = {
      createDynamicClient: sinon.stub().resolves({
        clientId: 'cid',
        clientSecret: 'secret',
        name: 'Cursor',
        redirectUris: ['http://127.0.0.1/cb'],
        allowedGrantTypes: [
          OAuthGrantType.AUTHORIZATION_CODE,
          OAuthGrantType.REFRESH_TOKEN,
        ],
        allowedScopes: ['user:read'],
      }),
    }
    mockScopeValidatorService = {
      parseScopes: sinon.stub().callsFake((s: string) => s.split(' ').filter(Boolean)),
      getAllowedScopeNamesForRole: sinon.stub().returns([
        'conversation:chat',
        'semantic:write',
        'kb:read',
        'user:read',
        'connector:read',
      ]),
    }
    service = new OAuthDcrService(
      createMockLogger(),
      mockOAuthAppService,
      mockScopeValidatorService,
      {
        mcpScopes: [
          'conversation:chat',
          'semantic:write',
          'kb:read',
          'user:read',
          'connector:read',
        ],
      } as any,
    )
    const orgQuery: any = {
      sort: sinon.stub().returnsThis(),
      select: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves({ _id: orgId }),
    }
    sinon.stub(Org, 'findOne').returns(orgQuery)
    const userQuery: any = {
      sort: sinon.stub().returnsThis(),
      select: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves({ _id: userId }),
    }
    sinon.stub(Users, 'findOne').returns(userQuery)
  })

  afterEach(() => {
    sinon.restore()
    setUserRepository()
    setIdentityPort()
    delete process.env.PIPESHUB_ENABLE_DCR
  })

  // The endpoint has no session, so the owner is read in the install's organisation, which PostgreSQL needs bound.
  it('reads the owner in the install organisation', async () => {
    process.env.PIPESHUB_ENABLE_DCR = 'true'
    setIdentityPort({ oldestOrgId: async () => 'org-1' } as any)
    let bound: string | undefined
    setUserRepository({
      getOldestId: async () => {
        bound = getCurrentOrgId()
        return 'user-1'
      },
    } as any)

    await service.register({ client_name: 'Cursor', redirect_uris: ['http://127.0.0.1/cb'], scope: 'user:read' })

    expect(bound).to.equal('org-1')
    expect(mockOAuthAppService.createDynamicClient.firstCall.args[0].orgId).to.equal('org-1')
  })

  it('should refuse when DCR is unset', async () => {
    try {
      await service.register({ client_name: 'Cursor' })
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(ForbiddenError)
    }
  })

  it('should register a public client without returning a secret', async () => {
    process.env.PIPESHUB_ENABLE_DCR = 'true'
    const result = await service.register({
      client_name: 'Cursor',
      redirect_uris: ['http://127.0.0.1/cb'],
      token_endpoint_auth_method: 'none',
      scope: 'user:read',
    })
    expect(result.client_id).to.equal('cid')
    expect(result.client_secret).to.equal(undefined)
    expect(result.token_endpoint_auth_method).to.equal('none')
    expect(result.response_types).to.deep.equal(['code'])
    expect(mockOAuthAppService.createDynamicClient.firstCall.args[0].isConfidential).to.be.false
  })

  it('should omit response_types code when authorization_code is not granted', async () => {
    process.env.PIPESHUB_ENABLE_DCR = 'true'
    mockOAuthAppService.createDynamicClient.resolves({
      clientId: 'cid',
      clientSecret: 'secret',
      name: 'CLI',
      redirectUris: [],
      allowedGrantTypes: [OAuthGrantType.REFRESH_TOKEN],
      allowedScopes: ['user:read'],
    })
    const result = await service.register({
      client_name: 'CLI',
      grant_types: ['refresh_token'],
      token_endpoint_auth_method: 'none',
      scope: 'user:read',
    })
    expect(result.response_types).to.deep.equal([])
    expect(result.grant_types).to.deep.equal([OAuthGrantType.REFRESH_TOKEN])
  })

  it('should reject response_type code without authorization_code', async () => {
    process.env.PIPESHUB_ENABLE_DCR = 'true'
    try {
      await service.register({
        client_name: 'CLI',
        grant_types: ['refresh_token'],
        response_types: ['code'],
      })
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(BadRequestError)
    }
  })

  it('should reject unsupported response_types', async () => {
    process.env.PIPESHUB_ENABLE_DCR = 'true'
    try {
      await service.register({
        client_name: 'Cursor',
        response_types: ['token'],
      })
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(BadRequestError)
    }
  })

  it('should reject client_credentials', async () => {
    process.env.PIPESHUB_ENABLE_DCR = 'true'
    try {
      await service.register({
        grant_types: ['client_credentials'],
        client_name: 'bad',
      })
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(BadRequestError)
    }
  })

  it('should refuse when DCR is disabled', async () => {
    process.env.PIPESHUB_ENABLE_DCR = 'false'
    try {
      await service.register({ client_name: 'Cursor' })
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(ForbiddenError)
    }
  })

  it('should refuse when DCR is a truthy string other than true', async () => {
    for (const value of ['TRUE', '1', 'yes']) {
      process.env.PIPESHUB_ENABLE_DCR = value
      try {
        await service.register({ client_name: 'Cursor' })
        expect.fail(`should have thrown for ${value}`)
      } catch (err) {
        expect(err).to.be.instanceOf(ForbiddenError)
      }
    }
    expect(mockOAuthAppService.createDynamicClient.called).to.be.false
  })

  describe('resolveAuthorizeClient (client ID metadata documents)', () => {
    const CID = 'https://client.example.com/oauth/metadata.json'
    const doc = {
      client_id: CID,
      client_name: 'Example Client',
      client_uri: 'https://client.example.com',
      redirect_uris: ['http://localhost/callback'],
      grant_types: ['authorization_code', 'refresh_token', 'urn:ietf:params:oauth:grant-type:device_code'],
      token_endpoint_auth_method: 'none',
      scope: 'user:read kb:read',
    }
    let stored: any
    let setDynamicGrant: sinon.SinonStub

    beforeEach(() => {
      delete process.env.PIPESHUB_ENABLE_CIMD
      stored = null
      setDynamicGrant = sinon.stub().resolves()
      setClientMetadataGetter(async () => JSON.stringify(doc))
      setOAuthAppRepository({ findLiveByClientId: async () => stored, setDynamicGrant } as any)
      setIdentityPort({ oldestOrgId: async () => 'org-1' } as any)
      setUserRepository({ getOldestId: async () => 'user-1' } as any)
      mockOAuthAppService.validateDcrRedirectUris = sinon.stub()
      mockOAuthAppService.getAppByClientId = sinon.stub().callsFake(async (id: string) => {
        if (id !== CID || !stored) throw new InvalidClientError('Invalid client_id')
        return stored
      })
      mockOAuthAppService.createDynamicClient = sinon.stub().callsFake(async (p: any) => {
        stored = { ...p, _id: 'app-1', name: 'stored name', isDynamic: true, status: 'active' }
        return stored
      })
    })

    afterEach(() => {
      setClientMetadataGetter()
      setOAuthAppRepository()
      delete process.env.PIPESHUB_ENABLE_CIMD
    })

    it('stores nothing until store(), then a public dynamic app under the URL', async () => {
      const { app, store } = await service.resolveAuthorizeClient(CID)

      expect(mockOAuthAppService.createDynamicClient.called).to.be.false
      expect(app.clientId).to.equal(CID)
      expect(app.name).to.equal('Example Client')
      expect(app.isDynamic).to.be.true
      expect(app.allowedScopes).to.deep.equal(['user:read', 'kb:read'])
      expect(mockOAuthAppService.validateDcrRedirectUris.calledWith(doc.redirect_uris)).to.be.true

      await store()
      const created = mockOAuthAppService.createDynamicClient.firstCall.args[0]
      expect(created.clientId).to.equal(CID)
      expect(created.isConfidential).to.be.false
      expect(created.orgId).to.equal('org-1')
      expect(created.allowedGrantTypes).to.deep.equal([
        OAuthGrantType.AUTHORIZATION_CODE,
        OAuthGrantType.REFRESH_TOKEN,
      ])
      expect(created.allowedScopes).to.deep.equal(['user:read', 'kb:read'])
    })

    it('allows refresh_token when the document names no grant types', async () => {
      setClientMetadataGetter(async () => JSON.stringify({ ...doc, grant_types: undefined }))
      const { app } = await service.resolveAuthorizeClient(CID)
      expect(app.allowedGrantTypes).to.deep.equal([
        OAuthGrantType.AUTHORIZATION_CODE,
        OAuthGrantType.REFRESH_TOKEN,
      ])
    })

    it('reuses the stored app and overlays the document fetched now', async () => {
      stored = {
        _id: 'app-1',
        orgId: 'org-1',
        clientId: CID,
        name: 'old name',
        redirectUris: ['http://localhost/old'],
        allowedScopes: ['user:read', 'kb:read'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.REFRESH_TOKEN],
        isDynamic: true,
      }

      const { app } = await service.resolveAuthorizeClient(CID)

      expect(mockOAuthAppService.createDynamicClient.called).to.be.false
      expect(setDynamicGrant.called).to.be.false
      expect(app.name).to.equal('Example Client')
      expect(app.redirectUris).to.deep.equal(['http://localhost/callback'])
      expect(app.homepageUrl).to.equal('https://client.example.com')
    })

    it('narrows a stored app to what the document and the instance allow now', async () => {
      stored = {
        _id: 'app-1',
        orgId: 'org-1',
        clientId: CID,
        allowedScopes: ['user:read', 'kb:read', 'kb:write'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.REFRESH_TOKEN],
        isDynamic: true,
      }

      const { app } = await service.resolveAuthorizeClient(CID)

      expect(app.allowedScopes).to.deep.equal(['user:read', 'kb:read'])
      expect(setDynamicGrant.calledOnceWith('org-1', 'app-1', {
        allowedScopes: ['user:read', 'kb:read'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.REFRESH_TOKEN],
      })).to.be.true
    })

    it('finds the app another request stored when its own create loses the race', async () => {
      mockOAuthAppService.createDynamicClient = sinon.stub().callsFake(async () => {
        stored = { clientId: CID, name: 'n', redirectUris: [], isDynamic: true }
        throw new Error('Duplicate key')
      })
      const { store } = await service.resolveAuthorizeClient(CID)
      const app = await store()
      expect(app.clientId).to.equal(CID)
    })

    it('gives a document without a scope the /mcp tool scopes, refresh included', async () => {
      setClientMetadataGetter(async () => JSON.stringify({ ...doc, scope: undefined }))
      ;(service as any).appConfig.mcpScopes = ['openid', 'offline_access', 'kb:read', 'user:read', 'org:admin']
      mockScopeValidatorService.getAllowedScopeNamesForRole.returns(['offline_access', 'kb:read', 'user:read'])

      const { app } = await service.resolveAuthorizeClient(CID)

      expect(app.allowedScopes).to.deep.equal(['offline_access', 'kb:read', 'user:read'])
    })

    it('surfaces a create failure that is not a lost race', async () => {
      mockOAuthAppService.createDynamicClient = sinon.stub().rejects(new Error('db down'))
      const { store } = await service.resolveAuthorizeClient(CID)
      try {
        await store()
        expect.fail('should have thrown')
      } catch (e) {
        expect((e as Error).message).to.equal('db down')
      }
    })

    it('rejects a document without the authorization_code grant', async () => {
      setClientMetadataGetter(async () => JSON.stringify({ ...doc, grant_types: ['refresh_token'] }))
      try {
        await service.resolveAuthorizeClient(CID)
        expect.fail('should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(InvalidClientError)
      }
      expect(mockOAuthAppService.createDynamicClient.called).to.be.false
    })

    it('looks the client id up as a registered client when CIMD is off', async () => {
      process.env.PIPESHUB_ENABLE_CIMD = 'false'
      let fetched = false
      setClientMetadataGetter(async () => {
        fetched = true
        return JSON.stringify(doc)
      })
      try {
        await service.resolveAuthorizeClient(CID)
        expect.fail('should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(InvalidClientError)
      }
      expect(fetched).to.be.false
    })

    it('passes a plain client id straight to the registered-client lookup', async () => {
      mockOAuthAppService.getAppByClientId = sinon.stub().resolves({ clientId: 'abc' })
      const { app, store } = await service.resolveAuthorizeClient('abc')
      expect(app.clientId).to.equal('abc')
      expect(await store()).to.equal(app)
    })
  })
})
