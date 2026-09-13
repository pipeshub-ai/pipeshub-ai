import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { OAuthGrantService } from '../../../../src/modules/oauth_provider/services/oauth.grant.service'
import { OAuthRefreshToken } from '../../../../src/modules/oauth_provider/schema/oauth.refresh_token.schema'
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema'
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'
import { createMockLogger } from '../../../helpers/mock-logger'

describe('OAuthGrantService', () => {
  let service: OAuthGrantService
  let mockLogger: any

  const orgId = new Types.ObjectId().toString()
  const userId = new Types.ObjectId().toString()
  const clientId = 'client-agent-123'

  beforeEach(() => {
    mockLogger = createMockLogger()
    service = new OAuthGrantService(mockLogger)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('listUserGrants', () => {
    it('returns active refresh tokens with app metadata and lastUsedAt', async () => {
      const rtId = new Types.ObjectId()
      const mockRefreshToken = {
        _id: rtId,
        clientId,
        userId: new Types.ObjectId(userId),
        orgId: new Types.ObjectId(orgId),
        scopes: ['read', 'write'],
        createdAt: new Date('2026-09-10T10:00:00Z'),
        expiresAt: new Date('2026-10-10T10:00:00Z'),
        isRevoked: false,
      }

      sinon.stub(OAuthRefreshToken, 'find').returns({
        sort: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([mockRefreshToken]),
      } as any)

      sinon.stub(OAuthAccessToken, 'find').returns({
        sort: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      } as any)

      const mockApp = {
        clientId,
        name: 'Coding Agent',
        description: 'Test agent',
        logoUrl: 'https://example.com/logo.png',
        isConfidential: false,
      }
      sinon.stub(OAuthApp, 'find').returns({
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([mockApp]),
      } as any)

      const lastUsed = new Date('2026-09-12T12:00:00Z')
      sinon.stub(OAuthAccessToken, 'aggregate').resolves([
        { _id: clientId, lastUsedAt: lastUsed },
      ])

      const result = await service.listUserGrants(orgId, userId)

      expect(result).to.have.length(1)
      expect(result[0]).to.deep.include({
        id: rtId.toString(),
        clientId,
        appName: 'Coding Agent',
        appDescription: 'Test agent',
        appLogoUrl: 'https://example.com/logo.png',
        isConfidential: false,
        scopes: ['read', 'write'],
        lastUsedAt: lastUsed,
      })
    })

    it('returns standalone access tokens when no refresh token exists', async () => {
      const atId = new Types.ObjectId()
      const mockAccessToken = {
        _id: atId,
        clientId: 'standalone-client',
        userId: new Types.ObjectId(userId),
        orgId: new Types.ObjectId(orgId),
        scopes: ['openid'],
        createdAt: new Date('2026-09-11T10:00:00Z'),
        expiresAt: new Date('2026-09-11T11:00:00Z'),
        lastUsedAt: new Date('2026-09-11T10:05:00Z'),
        isRevoked: false,
      }

      sinon.stub(OAuthRefreshToken, 'find').returns({
        sort: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      } as any)

      sinon.stub(OAuthAccessToken, 'find').returns({
        sort: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([mockAccessToken]),
      } as any)

      sinon.stub(OAuthApp, 'find').returns({
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      } as any)

      sinon.stub(OAuthAccessToken, 'aggregate').resolves([])

      const result = await service.listUserGrants(orgId, userId)

      expect(result).to.have.length(1)
      expect(result[0].id).to.equal(atId.toString())
      expect(result[0].appName).to.equal('standalone-client')
    })
  })

  describe('revokeUserGrant', () => {
    it('throws NotFoundError when grantId is invalid format', async () => {
      try {
        await service.revokeUserGrant(orgId, userId, 'invalid-id')
        expect.fail('should have thrown')
      } catch (err) {
        expect(err).to.be.instanceOf(NotFoundError)
      }
    })

    it('revokes refresh token and associated access tokens', async () => {
      const grantId = new Types.ObjectId().toString()
      const mockRt = {
        _id: new Types.ObjectId(grantId),
        clientId,
        isRevoked: false,
        save: sinon.stub().resolves(),
      }

      sinon.stub(OAuthRefreshToken, 'findOne').resolves(mockRt as any)
      const updateManyStub = sinon.stub(OAuthAccessToken, 'updateMany').resolves({} as any)

      await service.revokeUserGrant(orgId, userId, grantId, 'test reason')

      expect(mockRt.isRevoked).to.be.true
      expect(mockRt.save.calledOnce).to.be.true
      expect(updateManyStub.calledOnce).to.be.true
    })

    it('revokes standalone access token when refresh token not found', async () => {
      const grantId = new Types.ObjectId().toString()
      sinon.stub(OAuthRefreshToken, 'findOne').resolves(null)

      const mockAt = {
        _id: new Types.ObjectId(grantId),
        clientId,
        isRevoked: false,
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthAccessToken, 'findOne').resolves(mockAt as any)

      await service.revokeUserGrant(orgId, userId, grantId)

      expect(mockAt.isRevoked).to.be.true
      expect(mockAt.save.calledOnce).to.be.true
    })

    it('throws NotFoundError when grant does not exist or is already revoked', async () => {
      const grantId = new Types.ObjectId().toString()
      sinon.stub(OAuthRefreshToken, 'findOne').resolves(null)
      sinon.stub(OAuthAccessToken, 'findOne').resolves(null)

      try {
        await service.revokeUserGrant(orgId, userId, grantId)
        expect.fail('should have thrown')
      } catch (err) {
        expect(err).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('listAllGrants', () => {
    it('returns paginated list across org with user owner metadata', async () => {
      const userObjId = new Types.ObjectId()
      const rtId = new Types.ObjectId()
      const mockRefreshToken = {
        _id: rtId,
        clientId,
        userId: userObjId,
        orgId: new Types.ObjectId(orgId),
        scopes: ['read'],
        createdAt: new Date('2026-09-10T10:00:00Z'),
        expiresAt: new Date('2026-10-10T10:00:00Z'),
        isRevoked: false,
      }

      sinon.stub(OAuthRefreshToken, 'find').returns({
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([mockRefreshToken]),
      } as any)
      sinon.stub(OAuthRefreshToken, 'countDocuments').resolves(1)

      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([
          {
            _id: userObjId,
            email: 'user@example.com',
            fullName: 'Test User',
            isDeleted: false,
          },
        ]),
      } as any)

      sinon.stub(OAuthApp, 'find').returns({
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([
          {
            clientId,
            name: 'Coding Agent',
            isConfidential: false,
          },
        ]),
      } as any)

      sinon.stub(OAuthAccessToken, 'aggregate').resolves([])

      const result = await service.listAllGrants(orgId, 1, 10)

      expect(result.data).to.have.length(1)
      expect(result.data[0]).to.deep.include({
        id: rtId.toString(),
        clientId,
        appName: 'Coding Agent',
        userId: userObjId.toString(),
        ownerEmail: 'user@example.com',
        ownerFullName: 'Test User',
        ownerDeleted: false,
      })
      expect(result.pagination).to.deep.include({
        page: 1,
        limit: 10,
        total: 1,
        totalPages: 1,
      })
    })

    it('marks ownerDeleted true when user has isDeleted flag', async () => {
      const userObjId = new Types.ObjectId()
      const rtId = new Types.ObjectId()
      const mockRefreshToken = {
        _id: rtId,
        clientId,
        userId: userObjId,
        orgId: new Types.ObjectId(orgId),
        scopes: ['read'],
        createdAt: new Date(),
        expiresAt: new Date(Date.now() + 100000),
        isRevoked: false,
      }

      sinon.stub(OAuthRefreshToken, 'find').returns({
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([mockRefreshToken]),
      } as any)
      sinon.stub(OAuthRefreshToken, 'countDocuments').resolves(1)

      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([
          {
            _id: userObjId,
            email: 'departed@example.com',
            fullName: 'Departed User',
            isDeleted: true,
          },
        ]),
      } as any)

      sinon.stub(OAuthApp, 'find').returns({
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      } as any)
      sinon.stub(OAuthAccessToken, 'aggregate').resolves([])

      const result = await service.listAllGrants(orgId)
      expect(result.data[0].ownerDeleted).to.be.true
    })
  })

  describe('adminRevokeGrant', () => {
    it('allows admin to revoke any grant in the organization', async () => {
      const grantId = new Types.ObjectId().toString()
      const adminUserId = new Types.ObjectId().toString()
      const targetUserObjId = new Types.ObjectId()

      const mockRt = {
        _id: new Types.ObjectId(grantId),
        userId: targetUserObjId,
        clientId,
        isRevoked: false,
        save: sinon.stub().resolves(),
      }

      sinon.stub(OAuthRefreshToken, 'findOne').resolves(mockRt as any)
      const updateManyStub = sinon.stub(OAuthAccessToken, 'updateMany').resolves({} as any)

      await service.adminRevokeGrant(orgId, adminUserId, grantId, 'incident response')

      expect(mockRt.isRevoked).to.be.true
      expect(mockRt.save.calledOnce).to.be.true
      expect(updateManyStub.calledOnce).to.be.true
    })

    it('throws NotFoundError on invalid grantId', async () => {
      try {
        await service.adminRevokeGrant(orgId, userId, 'bad-id')
        expect.fail('should have thrown')
      } catch (err) {
        expect(err).to.be.instanceOf(NotFoundError)
      }
    })
  })
})
