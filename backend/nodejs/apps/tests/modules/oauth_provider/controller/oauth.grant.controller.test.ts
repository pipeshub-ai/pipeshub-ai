import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OAuthGrantController } from '../../../../src/modules/oauth_provider/controller/oauth.grant.controller'

describe('OAuthGrantController', () => {
  let controller: OAuthGrantController
  let mockLogger: any
  let mockOAuthGrantService: any
  let mockReq: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
    mockOAuthGrantService = {
      listUserGrants: sinon.stub(),
      revokeUserGrant: sinon.stub(),
      listAllGrants: sinon.stub(),
      adminRevokeGrant: sinon.stub(),
    }
    controller = new OAuthGrantController(mockLogger, mockOAuthGrantService)
    mockReq = {
      user: { orgId: 'org-123', userId: 'user-456' },
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

  describe('listGrants', () => {
    it('returns grants list for authenticated user', async () => {
      const mockGrants = [
        { id: 'g-1', clientId: 'agent-1', appName: 'Agent 1' },
      ]
      mockOAuthGrantService.listUserGrants.resolves(mockGrants)

      await controller.listGrants(mockReq, mockRes, mockNext)

      expect(mockOAuthGrantService.listUserGrants.calledWith('org-123', 'user-456')).to.be.true
      expect(mockRes.json.calledWith({ grants: mockGrants })).to.be.true
    })

    it('forwards errors to next middleware', async () => {
      const err = new Error('database failed')
      mockOAuthGrantService.listUserGrants.rejects(err)

      await controller.listGrants(mockReq, mockRes, mockNext)

      expect(mockNext.calledWith(err)).to.be.true
    })
  })

  describe('revokeGrant', () => {
    it('revokes grant and returns success message', async () => {
      mockReq.params.grantId = 'g-1'
      mockReq.body = { reason: 'suspicious activity' }
      mockOAuthGrantService.revokeUserGrant.resolves()

      await controller.revokeGrant(mockReq, mockRes, mockNext)

      expect(
        mockOAuthGrantService.revokeUserGrant.calledWith(
          'org-123',
          'user-456',
          'g-1',
          'suspicious activity',
        ),
      ).to.be.true
      expect(
        mockRes.json.calledWith({
          message: 'OAuth grant revoked successfully',
        }),
      ).to.be.true
    })

    it('forwards error to next middleware', async () => {
      mockReq.params.grantId = 'g-1'
      const err = new Error('not found')
      mockOAuthGrantService.revokeUserGrant.rejects(err)

      await controller.revokeGrant(mockReq, mockRes, mockNext)

      expect(mockNext.calledWith(err)).to.be.true
    })
  })

  describe('adminListGrants', () => {
    it('parses pagination query params and returns org grants', async () => {
      mockReq.query = { page: '2', limit: '25' }
      const mockResult = {
        data: [{ id: 'g-1' }],
        pagination: { page: 2, limit: 25, total: 30, totalPages: 2 },
      }
      mockOAuthGrantService.listAllGrants.resolves(mockResult)

      await controller.adminListGrants(mockReq, mockRes, mockNext)

      expect(mockOAuthGrantService.listAllGrants.calledWith('org-123', 2, 25)).to.be.true
      expect(mockRes.json.calledWith(mockResult)).to.be.true
    })

    it('forwards error to next middleware', async () => {
      const err = new Error('fail')
      mockOAuthGrantService.listAllGrants.rejects(err)

      await controller.adminListGrants(mockReq, mockRes, mockNext)

      expect(mockNext.calledWith(err)).to.be.true
    })
  })

  describe('adminRevokeGrant', () => {
    it('calls service adminRevokeGrant with admin user id and returns success', async () => {
      mockReq.params.grantId = 'g-1'
      mockReq.body = { reason: 'incident response' }
      mockOAuthGrantService.adminRevokeGrant.resolves()

      await controller.adminRevokeGrant(mockReq, mockRes, mockNext)

      expect(
        mockOAuthGrantService.adminRevokeGrant.calledWith(
          'org-123',
          'user-456',
          'g-1',
          'incident response',
        ),
      ).to.be.true
      expect(
        mockRes.json.calledWith({
          message: 'OAuth grant revoked successfully',
        }),
      ).to.be.true
    })

    it('forwards error to next middleware', async () => {
      mockReq.params.grantId = 'g-1'
      const err = new Error('fail')
      mockOAuthGrantService.adminRevokeGrant.rejects(err)

      await controller.adminRevokeGrant(mockReq, mockRes, mockNext)

      expect(mockNext.calledWith(err)).to.be.true
    })
  })
})
