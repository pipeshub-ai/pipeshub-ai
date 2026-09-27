import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ServiceTokenController } from '../../../../src/modules/oauth_provider/controller/service-token.controller'

describe('ServiceTokenController', () => {
  let controller: ServiceTokenController
  let mockServiceTokens: any
  let mockScopeValidator: any
  let mockReq: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockServiceTokens = {
      getAvailableScopes: sinon.stub(),
      createToken: sinon.stub(),
      listTokens: sinon.stub(),
      revokeToken: sinon.stub(),
    }
    mockScopeValidator = { getScopeDefinitions: sinon.stub().returns([]) }
    controller = new ServiceTokenController(mockServiceTokens, mockScopeValidator)
    mockReq = { user: { orgId: 'org-1', userId: 'user-1' }, query: {}, params: {}, body: {} }
    mockRes = { json: sinon.stub(), status: sinon.stub().returnsThis() }
    mockNext = sinon.stub()
  })

  afterEach(() => sinon.restore())

  describe('listScopes', () => {
    it('describes each permission, rather than returning bare names', async () => {
      mockServiceTokens.getAvailableScopes.resolves(['kb:read', 'semantic:write'])
      const defs = [
        {
          name: 'kb:read',
          description: 'Read knowledge bases and records',
          category: 'Knowledge Base',
          requiresUserConsent: true,
        },
        {
          name: 'semantic:write',
          description: 'Execute semantic search queries',
          category: 'Semantic',
          requiresUserConsent: true,
        },
      ]
      mockScopeValidator.getScopeDefinitions.returns(defs)

      await controller.listScopes(mockReq, mockRes, mockNext)

      expect(mockRes.json.calledWith({ scopes: defs })).to.be.true
    })

    it('describes exactly the scopes a service token may hold, and no others', async () => {
      // The wording comes from the shared scope catalogue, so a permission
      // reads the same here as on the personal access token screen. What this
      // endpoint decides is which scopes are on offer.
      mockServiceTokens.getAvailableScopes.resolves(['kb:read', 'user:read'])

      await controller.listScopes(mockReq, mockRes, mockNext)

      expect(
        mockScopeValidator.getScopeDefinitions.calledOnceWithExactly([
          'kb:read',
          'user:read',
        ]),
      ).to.be.true
    })

    it('hands a failure to the error handler rather than answering', async () => {
      mockServiceTokens.getAvailableScopes.rejects(new Error('etcd unreachable'))

      await controller.listScopes(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockRes.json.called).to.be.false
    })
  })
})
