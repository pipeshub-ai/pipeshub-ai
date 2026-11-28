import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service'
import {
  CLIENT_CREDENTIALS_KNOWLEDGE_LIMIT_UNAVAILABLE_MESSAGE,
  clientCredentialsKnowledgeLimitDefault,
  setClientCredentialsKnowledgeLimit,
} from '../../../../src/modules/oauth_provider/services/client-credentials-knowledge-limit'
import {
  OAuthApp,
  OAuthAppStatus,
  OAuthGrantType,
} from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { ForbiddenError } from '../../../../src/libs/errors/http.errors'
import { UnauthorizedClientError } from '../../../../src/libs/errors/oauth.errors'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { createMockLogger } from '../../../helpers/mock-logger'

/**
 * A knowledge limit on an app that uses client_credentials: no new or changed
 * limit, while a limit the app already had with the grant stays, unchanged,
 * and can be removed.
 */
describe('OAuthAppService - knowledge limit on client_credentials apps', () => {
  const orgId = new Types.ObjectId().toString()
  const userId = new Types.ObjectId().toString()
  const appId = new Types.ObjectId().toString()
  const serviceAccountId = new Types.ObjectId().toString()
  const limit = { apps: [new Types.ObjectId().toString()], recordGroups: [], records: [] }
  const otherLimit = { apps: [new Types.ObjectId().toString()], recordGroups: [], records: [] }

  let service: OAuthAppService

  beforeEach(() => {
    service = new OAuthAppService(
      createMockLogger() as any,
      { encrypt: sinon.stub().returns('encrypted') } as any,
      { validateRequestedScopes: sinon.stub(), getAllowedScopeNamesForRole: sinon.stub().returns(['org:read']) } as any,
      {
        revokeAllTokensForApp: sinon.stub().resolves(),
        revokeRefreshTokensWithoutKnowledgeLimit: sinon.stub().resolves(),
      } as any,
    )
    setClientCredentialsKnowledgeLimit(clientCredentialsKnowledgeLimitDefault)
    sinon.stub(Users, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().returns({ exec: sinon.stub().resolves({ isDisabled: false }) }),
      }),
    } as any)
  })

  afterEach(() => sinon.restore())

  async function refusal(run: () => Promise<unknown>): Promise<unknown> {
    try {
      await run()
      return undefined
    } catch (error) {
      return error
    }
  }

  describe('createApp', () => {
    function create(grants: OAuthGrantType[], knowledgeLimit?: typeof limit) {
      return service.createApp(orgId, userId, true, {
        name: 'Support bot',
        allowedScopes: ['org:read'],
        allowedGrantTypes: grants,
        redirectUris: grants.includes(OAuthGrantType.AUTHORIZATION_CODE) ? ['https://app.example.com/cb'] : [],
        ...(grants.includes(OAuthGrantType.CLIENT_CREDENTIALS) && { tokenIdentityUserId: serviceAccountId }),
        knowledgeLimit,
      })
    }

    it('refuses a client_credentials app with a limit, and writes nothing', async () => {
      const createStub = sinon.stub(OAuthApp, 'create')

      const error = await refusal(() => create([OAuthGrantType.CLIENT_CREDENTIALS], limit))

      expect(error).to.be.instanceOf(ForbiddenError)
      expect((error as Error).message).to.equal(CLIENT_CREDENTIALS_KNOWLEDGE_LIMIT_UNAVAILABLE_MESSAGE)
      expect(createStub.called).to.equal(false)
    })

    it('creates a client_credentials app without a limit', async () => {
      const createStub = sinon.stub(OAuthApp, 'create').resolves({ _id: new Types.ObjectId() } as any)

      await create([OAuthGrantType.CLIENT_CREDENTIALS])

      expect(createStub.calledOnce).to.equal(true)
    })

    it('creates an authorization_code app with a limit', async () => {
      const createStub = sinon.stub(OAuthApp, 'create').resolves({ _id: new Types.ObjectId() } as any)

      await create([OAuthGrantType.AUTHORIZATION_CODE], limit)

      expect((createStub.firstCall.args[0] as any).knowledgeLimit).to.deep.equal(limit)
    })
  })

  describe('updateApp', () => {
    function storedApp(grants: OAuthGrantType[], knowledgeLimit?: typeof limit) {
      return {
        _id: new Types.ObjectId(appId),
        clientId: 'cid',
        name: 'Support bot',
        redirectUris: [],
        allowedGrantTypes: grants,
        allowedScopes: ['org:read'],
        tokenIdentityUserId: new Types.ObjectId(serviceAccountId),
        knowledgeLimit,
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
    }

    function update(app: ReturnType<typeof storedApp>, data: Record<string, unknown>) {
      sinon.stub(OAuthApp, 'findOne').resolves(app as any)
      return refusal(() => service.updateApp(appId, orgId, userId, true, data as any))
    }

    it('keeps a limit the app already had with the grant, unchanged', async () => {
      const app = storedApp([OAuthGrantType.CLIENT_CREDENTIALS], limit)

      const error = await update(app, { name: 'Renamed', knowledgeLimit: { ...limit } })

      expect(error).to.equal(undefined)
      expect(app.save.calledOnce).to.equal(true)
    })

    it('removes a limit the app already had', async () => {
      const app = storedApp([OAuthGrantType.CLIENT_CREDENTIALS], limit)

      const error = await update(app, { knowledgeLimit: null })

      expect(error).to.equal(undefined)
      expect(app.knowledgeLimit).to.equal(undefined)
      expect(app.save.calledOnce).to.equal(true)
    })

    it('refuses to change a limit the app already had', async () => {
      const app = storedApp([OAuthGrantType.CLIENT_CREDENTIALS], limit)

      const error = await update(app, { knowledgeLimit: otherLimit })

      expect(error).to.be.instanceOf(ForbiddenError)
      expect(app.save.called).to.equal(false)
    })

    it('refuses to add a limit to a client_credentials app', async () => {
      const app = storedApp([OAuthGrantType.CLIENT_CREDENTIALS])

      const error = await update(app, { knowledgeLimit: limit })

      expect(error).to.be.instanceOf(ForbiddenError)
      expect(app.save.called).to.equal(false)
    })

    // Otherwise a limit set on an authorization_code app reaches a service
    // account one edit later.
    it('refuses to gain client_credentials while keeping a limit', async () => {
      const app = storedApp([OAuthGrantType.AUTHORIZATION_CODE], limit)

      const error = await update(app, {
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.CLIENT_CREDENTIALS],
      })

      expect(error).to.be.instanceOf(ForbiddenError)
      expect(app.save.called).to.equal(false)
    })

    it('changes the limit of an authorization_code app', async () => {
      const app = storedApp([OAuthGrantType.AUTHORIZATION_CODE], limit)

      const error = await update(app, { knowledgeLimit: otherLimit })

      expect(error).to.equal(undefined)
      expect(app.save.calledOnce).to.equal(true)
    })
  })

  describe('client_credentials tokens', () => {
    it('carry no limit', () => {
      expect(clientCredentialsKnowledgeLimitDefault.tokenLimit(undefined)).to.equal(undefined)
    })

    // Issuing one without the stored limit would reach more than the app may.
    it('are refused while the app still stores a limit', () => {
      expect(() => clientCredentialsKnowledgeLimitDefault.tokenLimit(limit)).to.throw(UnauthorizedClientError)
    })
  })
})
