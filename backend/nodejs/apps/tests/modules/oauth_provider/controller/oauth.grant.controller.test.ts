import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Response, NextFunction } from 'express'
import { OAuthGrantController } from '../../../../src/modules/oauth_provider/controller/oauth.grant.controller'
import { OAuthGrantService } from '../../../../src/modules/oauth_provider/services/oauth.grant.service'
import { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types'
import { UnauthorizedError } from '../../../../src/libs/errors/http.errors'
import { createMockLogger, MockLogger } from '../../../helpers/mock-logger'
import { Logger } from '../../../../src/libs/services/logger.service'

interface MockOAuthGrantService {
  listUserGrants: sinon.SinonStub
  revokeUserGrant: sinon.SinonStub
  listAllGrants: sinon.SinonStub
  adminRevokeGrant: sinon.SinonStub
}

interface MockResponse {
  json: sinon.SinonStub
  status: sinon.SinonStub
}

describe('OAuthGrantController', () => {
  let controller: OAuthGrantController
  let mockLogger: MockLogger
  let mockOAuthGrantService: MockOAuthGrantService
  let mockReq: AuthenticatedUserRequest
  let mockRes: MockResponse
  let mockNext: sinon.SinonStub

  const orgId = new Types.ObjectId().toString()
  const userId = new Types.ObjectId().toString()

  beforeEach(() => {
    mockLogger = createMockLogger()
    mockOAuthGrantService = {
      listUserGrants: sinon.stub(),
      revokeUserGrant: sinon.stub(),
      listAllGrants: sinon.stub(),
      adminRevokeGrant: sinon.stub(),
    }
    controller = new OAuthGrantController(
      mockLogger as unknown as Logger,
      mockOAuthGrantService as unknown as OAuthGrantService,
    )
    mockReq = {
      user: { orgId, userId },
      query: {},
      params: {},
      body: {},
    } as unknown as AuthenticatedUserRequest
    mockRes = {
      json: sinon.stub(),
      status: sinon.stub().returnsThis(),
    }
    mockNext = sinon.stub()
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('extractUser validation', () => {
    it('throws UnauthorizedError if user is missing', async () => {
      mockReq.user = undefined as unknown as typeof mockReq.user

      await controller.listGrants(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockNext.calledOnce).to.be.true
      const err = mockNext.firstCall.args[0]
      expect(err).to.be.instanceOf(UnauthorizedError)
      expect(err.message).to.equal('User not authenticated')
    })

    it('throws UnauthorizedError if orgId is not a valid ObjectId', async () => {
      mockReq.user = { orgId: 'not-valid-id', userId }

      await controller.listGrants(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockNext.calledOnce).to.be.true
      const err = mockNext.firstCall.args[0]
      expect(err).to.be.instanceOf(UnauthorizedError)
    })

    it('throws UnauthorizedError if userId is not a valid ObjectId', async () => {
      mockReq.user = { orgId, userId: 'invalid-user' }

      await controller.listGrants(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockNext.calledOnce).to.be.true
      const err = mockNext.firstCall.args[0]
      expect(err).to.be.instanceOf(UnauthorizedError)
    })
  })

  describe('listGrants', () => {
    it('returns grants list for authenticated user', async () => {
      const mockGrants = [
        { id: 'g-1', clientId: 'agent-1', appName: 'Agent 1' },
      ]
      mockOAuthGrantService.listUserGrants.resolves(mockGrants)

      await controller.listGrants(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockOAuthGrantService.listUserGrants.calledWith(orgId, userId)).to.be.true
      expect(mockRes.json.calledWith({ grants: mockGrants })).to.be.true
    })

    it('forwards errors to next middleware', async () => {
      const err = new Error('database failed')
      mockOAuthGrantService.listUserGrants.rejects(err)

      await controller.listGrants(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockNext.calledWith(err)).to.be.true
    })
  })

  describe('revokeGrant', () => {
    it('revokes grant and returns success message', async () => {
      mockReq.params.grantId = 'g-1'
      mockReq.body = { reason: 'suspicious activity' }
      mockOAuthGrantService.revokeUserGrant.resolves()

      await controller.revokeGrant(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(
        mockOAuthGrantService.revokeUserGrant.calledWith(
          orgId,
          userId,
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

      await controller.revokeGrant(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

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

      await controller.adminListGrants(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockOAuthGrantService.listAllGrants.calledWith(orgId, 2, 25)).to.be.true
      expect(mockRes.json.calledWith(mockResult)).to.be.true
    })

    it('forwards error to next middleware', async () => {
      const err = new Error('fail')
      mockOAuthGrantService.listAllGrants.rejects(err)

      await controller.adminListGrants(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockNext.calledWith(err)).to.be.true
    })
  })

  describe('adminRevokeGrant', () => {
    it('calls service adminRevokeGrant with admin user id and returns success', async () => {
      mockReq.params.grantId = 'g-1'
      mockReq.body = { reason: 'incident response' }
      mockOAuthGrantService.adminRevokeGrant.resolves()

      await controller.adminRevokeGrant(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(
        mockOAuthGrantService.adminRevokeGrant.calledWith(
          orgId,
          userId,
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

      await controller.adminRevokeGrant(
        mockReq,
        mockRes as unknown as Response,
        mockNext as unknown as NextFunction,
      )

      expect(mockNext.calledWith(err)).to.be.true
    })
  })
})
