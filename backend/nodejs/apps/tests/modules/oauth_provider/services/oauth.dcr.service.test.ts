import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { OAuthDcrService } from '../../../../src/modules/oauth_provider/services/oauth.dcr.service'
import { OAuthGrantType } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { BadRequestError, ForbiddenError } from '../../../../src/libs/errors/http.errors'
import { createMockLogger } from '../../../helpers/mock-logger'

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
      lean: sinon.stub().resolves([{ _id: orgId }]),
    }
    sinon.stub(Org, 'find').returns(orgQuery)
    const userQuery: any = {
      sort: sinon.stub().returnsThis(),
      select: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves({ _id: userId }),
    }
    sinon.stub(Users, 'findOne').returns(userQuery)
  })

  afterEach(() => {
    sinon.restore()
    delete process.env.PIPESHUB_ENABLE_DCR
  })

  it('should register a public client without returning a secret', async () => {
    const result = await service.register({
      client_name: 'Cursor',
      redirect_uris: ['http://127.0.0.1/cb'],
      token_endpoint_auth_method: 'none',
      scope: 'user:read',
    })
    expect(result.client_id).to.equal('cid')
    expect(result.client_secret).to.equal(undefined)
    expect(result.token_endpoint_auth_method).to.equal('none')
    expect(mockOAuthAppService.createDynamicClient.firstCall.args[0].isConfidential).to.be.false
  })

  it('should reject client_credentials', async () => {
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
})
