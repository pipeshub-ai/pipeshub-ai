import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { FirstPartyDeviceAppService } from '../../../../src/modules/oauth_provider/services/oauth.first_party_device.service'
import {
  OAuthApp,
  OAuthGrantType,
} from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { FIRST_PARTY_DEVICE_CLIENT_ID } from '../../../../src/modules/oauth_provider/constants/constants'
import { AgentMcpScopes } from '../../../../src/modules/oauth_provider/config/scopes.config'
import { createMockLogger } from '../../../helpers/mock-logger'

describe('FirstPartyDeviceAppService', () => {
  let service: FirstPartyDeviceAppService
  let mockEncryptionService: any
  const orgId = new Types.ObjectId()
  const userId = new Types.ObjectId()

  beforeEach(() => {
    mockEncryptionService = {
      encrypt: sinon.stub().returns('encrypted-secret'),
    }
    service = new FirstPartyDeviceAppService(
      createMockLogger(),
      mockEncryptionService,
      {
        getAllowedScopeNamesForRole: sinon.stub().returns([...AgentMcpScopes]),
      } as any,
      {
        mcpScopes: [...AgentMcpScopes],
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
  })

  it('should return the existing client_id without creating another app', async () => {
    sinon.stub(OAuthApp, 'findOne').resolves({
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
      allowedScopes: [...AgentMcpScopes],
    } as any)
    const create = sinon.stub(OAuthApp, 'create')
    const update = sinon.stub(OAuthApp, 'updateOne')

    const clientId = await service.getOrCreate()
    expect(clientId).to.equal(FIRST_PARTY_DEVICE_CLIENT_ID)
    expect(create.called).to.be.false
    expect(update.called).to.be.false
  })

  // An upgraded instance already has the app, stored with the preset of the
  // release that created it; device logins only get scopes on that row.
  it('adds preset scopes an existing app was stored without, and removes none', async () => {
    const appId = new Types.ObjectId()
    const oldPreset = AgentMcpScopes.filter((s) => s !== 'kb:write' && s !== 'kb:upload')
    sinon.stub(OAuthApp, 'findOne').resolves({
      _id: appId,
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
      allowedScopes: [...oldPreset, 'custom:kept'],
    } as any)
    const update = sinon.stub(OAuthApp, 'updateOne').resolves({} as any)

    await service.getOrCreate()
    expect(update.calledOnce).to.be.true
    expect(update.firstCall.args).to.deep.equal([
      { _id: appId },
      { $addToSet: { allowedScopes: { $each: ['kb:write', 'kb:upload'] } } },
    ])
  })

  it('does not add a preset scope the instance MCP_SCOPES leaves out', async () => {
    service = new FirstPartyDeviceAppService(
      createMockLogger(),
      mockEncryptionService,
      { getAllowedScopeNamesForRole: sinon.stub().returns([...AgentMcpScopes]) } as any,
      { mcpScopes: AgentMcpScopes.filter((s) => s !== 'kb:write' && s !== 'kb:upload') } as any,
    )
    sinon.stub(OAuthApp, 'findOne').resolves({
      _id: new Types.ObjectId(),
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
      allowedScopes: AgentMcpScopes.filter((s) => s !== 'kb:write' && s !== 'kb:upload'),
    } as any)
    const update = sinon.stub(OAuthApp, 'updateOne')

    await service.getOrCreate()
    expect(update.called).to.be.false
  })

  it('still returns the client_id when the scope update fails', async () => {
    sinon.stub(OAuthApp, 'findOne').resolves({
      _id: new Types.ObjectId(),
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
      allowedScopes: [],
    } as any)
    sinon.stub(OAuthApp, 'updateOne').rejects(new Error('mongo down'))

    expect(await service.getOrCreate()).to.equal(FIRST_PARTY_DEVICE_CLIENT_ID)
  })

  it('should create a public device-only app with the agent preset', async () => {
    sinon.stub(OAuthApp, 'findOne').resolves(null)
    const create = sinon.stub(OAuthApp, 'create').resolves({
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
    } as any)

    const clientId = await service.getOrCreate()
    expect(clientId).to.equal(FIRST_PARTY_DEVICE_CLIENT_ID)
    const data = create.firstCall.args[0] as Record<string, unknown>
    expect(data.clientId).to.equal(FIRST_PARTY_DEVICE_CLIENT_ID)
    expect(data.name).to.equal('PipesHub agent')
    expect(data.isConfidential).to.equal(false)
    expect(data.isDynamic).to.equal(false)
    expect(data.allowedGrantTypes).to.deep.equal([
      OAuthGrantType.DEVICE_CODE,
      OAuthGrantType.REFRESH_TOKEN,
    ])
    expect(data.allowedGrantTypes).to.not.include(
      OAuthGrantType.CLIENT_CREDENTIALS,
    )
    expect(data.allowedScopes).to.deep.equal([...AgentMcpScopes])
    expect(mockEncryptionService.encrypt.calledOnce).to.be.true
  })

  it('should return null when the instance has no org', async () => {
    ;(Org.find as sinon.SinonStub).returns({
      sort: sinon.stub().returnsThis(),
      select: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves([]),
    })
    sinon.stub(OAuthApp, 'findOne').resolves(null)
    const create = sinon.stub(OAuthApp, 'create')

    expect(await service.getOrCreate()).to.equal(null)
    expect(create.called).to.be.false
  })

  it('should return null when the org has no users yet', async () => {
    ;(Users.findOne as sinon.SinonStub).returns({
      sort: sinon.stub().returnsThis(),
      select: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves(null),
    })
    sinon.stub(OAuthApp, 'findOne').resolves(null)
    const create = sinon.stub(OAuthApp, 'create')

    expect(await service.getOrCreate()).to.equal(null)
    expect(create.called).to.be.false
  })

  it('should return the winner when two creates race on clientId', async () => {
    const findOne = sinon.stub(OAuthApp, 'findOne')
    findOne.onFirstCall().resolves(null)
    findOne.onSecondCall().resolves({
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
    } as any)
    sinon.stub(OAuthApp, 'create').rejects(new Error('E11000 duplicate key'))

    expect(await service.getOrCreate()).to.equal(FIRST_PARTY_DEVICE_CLIENT_ID)
  })

  it('should omit scopes that are not in MCP_SCOPES', async () => {
    service = new FirstPartyDeviceAppService(
      createMockLogger(),
      mockEncryptionService,
      {
        getAllowedScopeNamesForRole: sinon.stub().returns([...AgentMcpScopes]),
      } as any,
      {
        mcpScopes: ['user:read', 'kb:read'],
      } as any,
    )
    sinon.stub(OAuthApp, 'findOne').resolves(null)
    const create = sinon.stub(OAuthApp, 'create').resolves({
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
    } as any)

    await service.getOrCreate()
    const data = create.firstCall.args[0] as Record<string, unknown>
    expect(data.allowedScopes).to.deep.equal(['kb:read', 'user:read'])
  })

  it('should return null when the agent preset intersects MCP_SCOPES as empty', async () => {
    service = new FirstPartyDeviceAppService(
      createMockLogger(),
      mockEncryptionService,
      {
        getAllowedScopeNamesForRole: sinon.stub().returns([...AgentMcpScopes]),
      } as any,
      {
        mcpScopes: ['config:read'],
      } as any,
    )
    sinon.stub(OAuthApp, 'findOne').resolves(null)
    const create = sinon.stub(OAuthApp, 'create')

    expect(await service.getOrCreate()).to.equal(null)
    expect(create.called).to.be.false
  })
})
