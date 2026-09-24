import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { PatController } from '../../../../src/modules/oauth_provider/controller/pat.controller'

describe('PatController', () => {
  let controller: PatController
  let mockLogger: any
  let mockPatService: any
  let mockScopeValidatorService: any
  let mockReq: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockLogger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    mockPatService = {
      createToken: sinon.stub(),
      listTokens: sinon.stub(),
      revokeToken: sinon.stub(),
      getDefaultScopes: sinon.stub(),
      listAllTokens: sinon.stub(),
      adminRevokeToken: sinon.stub(),
    }
    mockScopeValidatorService = {
      getScopeDefinitions: sinon.stub().returns([]),
    }
    controller = new PatController(mockLogger, mockPatService, mockScopeValidatorService)
    mockReq = {
      user: { orgId: 'org-1', userId: 'user-1', fullName: 'Test User' },
      query: {},
      params: {},
      body: {},
    }
    mockRes = { json: sinon.stub(), status: sinon.stub().returnsThis() }
    mockNext = sinon.stub()
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('createToken', () => {
    it('creates a token and returns 201, forwarding fullName from req.user', async () => {
      const token = { id: 'tok-1', name: 'my token', scopes: [], createdAt: new Date(), expiresAt: new Date(), accessToken: 'raw' }
      mockPatService.createToken.resolves(token)
      mockReq.body = { name: 'my token' }

      await controller.createToken(mockReq, mockRes, mockNext)

      expect(mockPatService.createToken.firstCall.args).to.deep.equal([
        'org-1',
        'user-1',
        'Test User',
        { name: 'my token' },
      ])
      expect(mockRes.status.calledWith(201)).to.be.true
      expect(mockRes.json.calledWith({ message: 'Personal access token created successfully', token })).to.be.true
    })

    it('calls next on service error', async () => {
      mockPatService.createToken.rejects(new Error('fail'))
      await controller.createToken(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('listTokens', () => {
    it('returns the user\'s tokens', async () => {
      const tokens = [{ id: 'tok-1', name: 't', scopes: [], createdAt: new Date(), expiresAt: new Date() }]
      mockPatService.listTokens.resolves(tokens)

      await controller.listTokens(mockReq, mockRes, mockNext)

      expect(mockPatService.listTokens.calledWith('org-1', 'user-1')).to.be.true
      expect(mockRes.json.calledWith({ tokens })).to.be.true
    })

    it('calls next on service error', async () => {
      mockPatService.listTokens.rejects(new Error('fail'))
      await controller.listTokens(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('revokeToken', () => {
    it('revokes the token by id, forwarding an optional reason', async () => {
      mockPatService.revokeToken.resolves()
      mockReq.params = { tokenId: 'tok-1' }
      mockReq.body = { reason: 'rotated' }

      await controller.revokeToken(mockReq, mockRes, mockNext)

      expect(mockPatService.revokeToken.calledWith('org-1', 'user-1', 'tok-1', 'rotated')).to.be.true
      expect(mockRes.json.calledWith({ message: 'Personal access token revoked successfully' })).to.be.true
    })

    it('passes undefined reason when body has none', async () => {
      mockPatService.revokeToken.resolves()
      mockReq.params = { tokenId: 'tok-1' }

      await controller.revokeToken(mockReq, mockRes, mockNext)

      expect(mockPatService.revokeToken.calledWith('org-1', 'user-1', 'tok-1', undefined)).to.be.true
    })

    it('calls next on service error (e.g. NotFoundError)', async () => {
      mockPatService.revokeToken.rejects(new Error('not found'))
      mockReq.params = { tokenId: 'tok-1' }
      await controller.revokeToken(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('listScopes', () => {
    it('returns scope definitions for the org MCP scope set', async () => {
      mockPatService.getDefaultScopes.resolves(['kb:read'])
      const defs = [{ name: 'kb:read', description: 'd', category: 'c', requiresUserConsent: true }]
      mockScopeValidatorService.getScopeDefinitions.returns(defs)

      await controller.listScopes(mockReq, mockRes, mockNext)

      expect(mockScopeValidatorService.getScopeDefinitions.calledWith(['kb:read'])).to.be.true
      expect(mockRes.json.calledWith({ scopes: defs })).to.be.true
    })

    it('calls next on service error', async () => {
      mockPatService.getDefaultScopes.rejects(new Error('fail'))
      await controller.listScopes(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('adminListTokens', () => {
    it('returns every token in the org as a paginated response', async () => {
      const result = {
        data: [
          { id: 'tok-1', name: 't', scopes: [], createdAt: new Date(), expiresAt: new Date(), userId: 'user-2', ownerDeleted: false },
        ],
        pagination: { page: 1, limit: 100, total: 1, totalPages: 1 },
      }
      mockPatService.listAllTokens.resolves(result)

      await controller.adminListTokens(mockReq, mockRes, mockNext)

      expect(mockPatService.listAllTokens.calledWith('org-1', undefined, undefined)).to.be.true
      expect(mockRes.json.calledWith(result)).to.be.true
    })

    it('parses page/limit from the query string', async () => {
      mockPatService.listAllTokens.resolves({ data: [], pagination: { page: 2, limit: 25, total: 0, totalPages: 0 } })
      mockReq.query = { page: '2', limit: '25' }

      await controller.adminListTokens(mockReq, mockRes, mockNext)

      expect(mockPatService.listAllTokens.calledWith('org-1', 2, 25)).to.be.true
    })

    it('calls next on service error', async () => {
      mockPatService.listAllTokens.rejects(new Error('fail'))
      await controller.adminListTokens(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  describe('adminRevokeToken', () => {
    it('revokes any user\'s token by id, forwarding an optional reason', async () => {
      mockPatService.adminRevokeToken.resolves()
      mockReq.params = { tokenId: 'tok-1' }
      mockReq.body = { reason: 'departed employee' }

      await controller.adminRevokeToken(mockReq, mockRes, mockNext)

      expect(mockPatService.adminRevokeToken.calledWith('org-1', 'user-1', 'tok-1', 'departed employee')).to.be.true
      expect(mockRes.json.calledWith({ message: 'Personal access token revoked successfully' })).to.be.true
    })

    it('calls next on service error (e.g. NotFoundError)', async () => {
      mockPatService.adminRevokeToken.rejects(new Error('not found'))
      mockReq.params = { tokenId: 'tok-1' }
      await controller.adminRevokeToken(mockReq, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })
})
