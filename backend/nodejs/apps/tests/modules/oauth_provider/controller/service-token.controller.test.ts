import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import type { NextFunction, Response } from 'express'
import { ServiceTokenController } from '../../../../src/modules/oauth_provider/controller/service-token.controller'
import type { ServiceTokenService } from '../../../../src/modules/oauth_provider/services/service-token.service'
import type { ScopeValidatorService } from '../../../../src/modules/oauth_provider/services/scope.validator.service'
import type { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types'

/** Only the members this controller reaches for, each one a stub the test drives. */
interface ServiceTokenStubs {
  getAvailableScopes: sinon.SinonStub
  createToken: sinon.SinonStub
  listTokens: sinon.SinonStub
  revokeToken: sinon.SinonStub
}

interface ScopeValidatorStubs {
  getScopeDefinitions: sinon.SinonStub
}

interface ResponseStubs {
  json: sinon.SinonStub
  status: sinon.SinonStub
}

describe('ServiceTokenController', () => {
  let controller: ServiceTokenController
  let mockServiceTokens: ServiceTokenStubs
  let mockScopeValidator: ScopeValidatorStubs
  let mockReq: AuthenticatedUserRequest
  let mockRes: ResponseStubs
  let mockNext: sinon.SinonStub

  beforeEach(() => {
    mockServiceTokens = {
      getAvailableScopes: sinon.stub(),
      createToken: sinon.stub(),
      listTokens: sinon.stub(),
      revokeToken: sinon.stub(),
    }
    mockScopeValidator = { getScopeDefinitions: sinon.stub().returns([]) }
    controller = new ServiceTokenController(
      // The stubs stand in for the two services, carrying only the members the
      // controller calls; the cast is what keeps that partial shape honest
      // rather than widening either type to `any`.
      mockServiceTokens as unknown as ServiceTokenService,
      mockScopeValidator as unknown as ScopeValidatorService,
    )
    mockReq = {
      user: { orgId: 'org-1', userId: 'user-1' },
      query: {},
      params: {},
      body: {},
    } as unknown as AuthenticatedUserRequest
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

      await controller.listScopes(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockRes.json.calledWith({ scopes: defs })).to.be.true
    })

    it('describes exactly the scopes a service token may hold, and no others', async () => {
      // The wording comes from the shared scope catalogue, so a permission
      // reads the same here as on the personal access token screen. What this
      // endpoint decides is which scopes are on offer.
      mockServiceTokens.getAvailableScopes.resolves(['kb:read', 'user:read'])

      await controller.listScopes(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(
        mockScopeValidator.getScopeDefinitions.calledOnceWithExactly([
          'kb:read',
          'user:read',
        ]),
      ).to.be.true
    })

    it('hands a failure to the error handler rather than answering', async () => {
      mockServiceTokens.getAvailableScopes.rejects(new Error('etcd unreachable'))

      await controller.listScopes(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockNext.calledOnce).to.be.true
      expect(mockRes.json.called).to.be.false
    })
  })
})
