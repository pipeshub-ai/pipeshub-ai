import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose, { Types } from 'mongoose'
import { OAuthGrantService } from '../../../../src/modules/oauth_provider/services/oauth.grant.service'
import { OAuthRefreshToken, IOAuthRefreshToken } from '../../../../src/modules/oauth_provider/schema/oauth.refresh_token.schema'
import { OAuthAccessToken, IOAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema'
import { OAuthApp, IOAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { Users, User } from '../../../../src/modules/user_management/schema/users.schema'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'
import { createMockLogger, MockLogger } from '../../../helpers/mock-logger'
import { Logger } from '../../../../src/libs/services/logger.service'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'

function createRefreshTokenQueryFixture(
  docs: Partial<IOAuthRefreshToken>[],
): ReturnType<typeof OAuthRefreshToken.find> {
  const query = new mongoose.Query<IOAuthRefreshToken[], IOAuthRefreshToken>()
  sinon.stub(query, 'sort').returns(query)
  sinon.stub(query, 'skip').returns(query)
  sinon.stub(query, 'limit').returns(query)
  sinon.stub(query, 'select').returns(query)
  sinon.stub(query, 'lean').returns(query)
  sinon.stub(query, 'exec').resolves(docs as IOAuthRefreshToken[])
  return query
}

function createAccessTokenQueryFixture(
  docs: Partial<IOAuthAccessToken>[],
): ReturnType<typeof OAuthAccessToken.find> {
  const query = new mongoose.Query<IOAuthAccessToken[], IOAuthAccessToken>()
  sinon.stub(query, 'sort').returns(query)
  sinon.stub(query, 'skip').returns(query)
  sinon.stub(query, 'limit').returns(query)
  sinon.stub(query, 'select').returns(query)
  sinon.stub(query, 'lean').returns(query)
  sinon.stub(query, 'exec').resolves(docs as IOAuthAccessToken[])
  return query
}

function createOAuthAppQueryFixture(
  docs: Partial<IOAuthApp>[],
): ReturnType<typeof OAuthApp.find> {
  const query = new mongoose.Query<IOAuthApp[], IOAuthApp>()
  sinon.stub(query, 'sort').returns(query)
  sinon.stub(query, 'skip').returns(query)
  sinon.stub(query, 'limit').returns(query)
  sinon.stub(query, 'select').returns(query)
  sinon.stub(query, 'lean').returns(query)
  sinon.stub(query, 'exec').resolves(docs as IOAuthApp[])
  return query
}

function createUserQueryFixture(
  docs: Partial<User>[],
): ReturnType<typeof Users.find> {
  const query = new mongoose.Query<User[], User>()
  sinon.stub(query, 'sort').returns(query)
  sinon.stub(query, 'skip').returns(query)
  sinon.stub(query, 'limit').returns(query)
  sinon.stub(query, 'select').returns(query)
  sinon.stub(query, 'lean').returns(query)
  sinon.stub(query, 'exec').resolves(docs as User[])
  return query
}

describe('OAuthGrantService', () => {
  let service: OAuthGrantService
  let mockLogger: MockLogger
  let mockAppConfig: AppConfig

  const orgId = new Types.ObjectId().toString()
  const userId = new Types.ObjectId().toString()
  const clientId = 'client-agent-123'

  beforeEach(() => {
    mockLogger = createMockLogger()
    mockAppConfig = { rsAvailable: 'false' } as AppConfig
    service = new OAuthGrantService(mockLogger as unknown as Logger, mockAppConfig)
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

      sinon
        .stub(OAuthRefreshToken, 'find')
        .returns(createRefreshTokenQueryFixture([mockRefreshToken]))
      sinon
        .stub(OAuthAccessToken, 'find')
        .returns(createAccessTokenQueryFixture([]))

      const mockApp = {
        clientId,
        name: 'Coding Agent',
        description: 'Test agent',
        logoUrl: 'https://example.com/logo.png',
        isConfidential: false,
      }
      sinon
        .stub(OAuthApp, 'find')
        .returns(createOAuthAppQueryFixture([mockApp]))

      const lastUsed = new Date('2026-09-12T12:00:00Z')
      const aggregateStub = sinon.stub(OAuthAccessToken, 'aggregate').resolves([
        { _id: rtId, lastUsedAt: lastUsed },
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

      const pipeline = aggregateStub.firstCall.args[0]
      const matchStage = pipeline[0].$match
      expect(matchStage).to.have.property('isRevoked', false)
      expect(matchStage.parentRefreshTokenId.$in.map(String)).to.include(rtId.toString())

      const groupStage = pipeline[1].$group
      expect(groupStage._id).to.equal('$parentRefreshTokenId')
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

      sinon
        .stub(OAuthRefreshToken, 'find')
        .returns(createRefreshTokenQueryFixture([]))
      sinon
        .stub(OAuthAccessToken, 'find')
        .returns(createAccessTokenQueryFixture([mockAccessToken]))
      sinon.stub(OAuthApp, 'find').returns(createOAuthAppQueryFixture([]))
      sinon.stub(OAuthAccessToken, 'aggregate').resolves([])

      const result = await service.listUserGrants(orgId, userId)

      expect(result).to.have.length(1)
      expect(result[0].id).to.equal(atId.toString())
      expect(result[0].appName).to.equal('standalone-client')
    })

    it('suppresses access token only when parentRefreshTokenId matches active refresh token, preserving separate standalone access token for same client', async () => {
      const rtId = new Types.ObjectId()
      const atChildId = new Types.ObjectId()
      const atStandaloneId = new Types.ObjectId()

      const mockRefreshToken = {
        _id: rtId,
        clientId,
        userId: new Types.ObjectId(userId),
        orgId: new Types.ObjectId(orgId),
        scopes: ['read', 'offline_access'],
        createdAt: new Date('2026-09-10T10:00:00Z'),
        expiresAt: new Date('2026-10-10T10:00:00Z'),
        isRevoked: false,
      }

      const mockChildAccessToken = {
        _id: atChildId,
        clientId,
        userId: new Types.ObjectId(userId),
        orgId: new Types.ObjectId(orgId),
        scopes: ['read'],
        createdAt: new Date('2026-09-10T10:00:00Z'),
        expiresAt: new Date('2026-09-10T11:00:00Z'),
        parentRefreshTokenId: rtId,
        isRevoked: false,
      }

      const mockStandaloneAccessToken = {
        _id: atStandaloneId,
        clientId,
        userId: new Types.ObjectId(userId),
        orgId: new Types.ObjectId(orgId),
        scopes: ['read'],
        createdAt: new Date('2026-09-11T10:00:00Z'),
        expiresAt: new Date('2026-09-11T11:00:00Z'),
        parentRefreshTokenId: undefined,
        isRevoked: false,
      }

      sinon
        .stub(OAuthRefreshToken, 'find')
        .returns(createRefreshTokenQueryFixture([mockRefreshToken]))
      sinon
        .stub(OAuthAccessToken, 'find')
        .returns(
          createAccessTokenQueryFixture([
            mockChildAccessToken,
            mockStandaloneAccessToken,
          ]),
        )
      sinon.stub(OAuthApp, 'find').returns(createOAuthAppQueryFixture([]))
      sinon.stub(OAuthAccessToken, 'aggregate').resolves([])

      const result = await service.listUserGrants(orgId, userId)

      expect(result).to.have.length(2)
      const ids = result.map((g) => g.id)
      expect(ids).to.include(rtId.toString())
      expect(ids).to.include(atStandaloneId.toString())
      expect(ids).to.not.include(atChildId.toString())
    })

    it('aggregates lastUsedAt matching active access tokens grouped by parentRefreshTokenId', async () => {
      const rtId1 = new Types.ObjectId()
      const rtId2 = new Types.ObjectId()

      sinon.stub(OAuthRefreshToken, 'find').returns(
        createRefreshTokenQueryFixture([
          { _id: rtId1, clientId, userId: new Types.ObjectId(userId), orgId: new Types.ObjectId(orgId), isRevoked: false, createdAt: new Date(), expiresAt: new Date() } as any,
          { _id: rtId2, clientId, userId: new Types.ObjectId(userId), orgId: new Types.ObjectId(orgId), isRevoked: false, createdAt: new Date(), expiresAt: new Date() } as any,
        ])
      )
      sinon.stub(OAuthAccessToken, 'find').returns(createAccessTokenQueryFixture([]))
      sinon.stub(OAuthApp, 'find').returns(createOAuthAppQueryFixture([]))

      const aggregateStub = sinon.stub(OAuthAccessToken, 'aggregate').resolves([
        { _id: rtId1, lastUsedAt: new Date('2026-09-10T11:00:00Z') },
        { _id: rtId2, lastUsedAt: new Date('2026-09-11T11:00:00Z') },
      ])

      const result = await service.listUserGrants(orgId, userId)

      expect(result).to.have.length(2)
      expect(result.find((r) => r.id === rtId1.toString())?.lastUsedAt).to.deep.equal(new Date('2026-09-10T11:00:00Z'))
      expect(result.find((r) => r.id === rtId2.toString())?.lastUsedAt).to.deep.equal(new Date('2026-09-11T11:00:00Z'))

      const pipeline = aggregateStub.firstCall.args[0]
      const matchStage = pipeline[0].$match
      expect(matchStage).to.have.property('isRevoked', false)
      expect(matchStage).to.have.property('expiresAt')
      expect(matchStage.expiresAt).to.have.property('$gt')
      expect(matchStage.parentRefreshTokenId.$in.map(String)).to.include.members([rtId1.toString(), rtId2.toString()])

      const groupStage = pipeline[1].$group
      expect(groupStage._id).to.equal('$parentRefreshTokenId')
    })
  })

  describe('revokeUserGrant', () => {
    it('throws NotFoundError when grantId is invalid format', async () => {
      try {
        await service.revokeUserGrant(orgId, userId, 'invalid-id')
        expect.fail('should have thrown')
      } catch (err: unknown) {
        if (err instanceof Error) {
          expect(err).to.be.instanceOf(NotFoundError)
        } else {
          expect.fail('Thrown error is not an Error')
        }
      }
    })

    it('revokes refresh token and associated access tokens filtering by parentRefreshTokenId', async () => {
      const grantId = new Types.ObjectId().toString()
      const mockRt = new OAuthRefreshToken({
        _id: new Types.ObjectId(grantId),
        clientId,
        isRevoked: false,
      })
      sinon.stub(mockRt, 'save').resolves()

      sinon
        .stub(OAuthRefreshToken, 'findOne')
        .resolves(mockRt)
      const updateManyStub = sinon
        .stub(OAuthAccessToken, 'updateMany')
        .resolves()

      await service.revokeUserGrant(orgId, userId, grantId, 'test reason')

      expect(mockRt.isRevoked).to.be.true
      expect((mockRt.save as sinon.SinonStub).calledOnce).to.be.true
      expect(updateManyStub.calledOnce).to.be.true
      const filterArg = updateManyStub.firstCall.args[0]
      expect(filterArg).to.have.property('parentRefreshTokenId')
    })

    it('revokes standalone access token when refresh token not found', async () => {
      const grantId = new Types.ObjectId().toString()
      sinon.stub(OAuthRefreshToken, 'findOne').resolves(null)

      const mockAt = new OAuthAccessToken({
        _id: new Types.ObjectId(grantId),
        clientId,
        isRevoked: false,
      })
      sinon.stub(mockAt, 'save').resolves()
      sinon
        .stub(OAuthAccessToken, 'findOne')
        .resolves(mockAt)

      await service.revokeUserGrant(orgId, userId, grantId)

      expect(mockAt.isRevoked).to.be.true
      expect((mockAt.save as sinon.SinonStub).calledOnce).to.be.true
    })

    it('revokes refresh token and associated access tokens within a transaction when REPLICA_SET_AVAILABLE is true', async () => {
      mockAppConfig.rsAvailable = 'true'
      try {
        const mockSession = {
          withTransaction: sinon.stub().callsFake(async (fn: () => Promise<void>) => {
            await fn()
          }),
          endSession: sinon.stub().resolves(),
        }
        sinon.stub(mongoose, 'startSession').resolves(mockSession as any)

        const grantId = new Types.ObjectId().toString()
        const mockRt = new OAuthRefreshToken({
          _id: new Types.ObjectId(grantId),
          clientId,
          isRevoked: false,
        })
        sinon.stub(mockRt, 'save').resolves()
        sinon.stub(OAuthRefreshToken, 'findOne').resolves(mockRt)
        const updateManyStub = sinon
          .stub(OAuthAccessToken, 'updateMany')
          .resolves()

        await service.revokeUserGrant(orgId, userId, grantId, 'test reason')

        expect(mockSession.withTransaction.calledOnce).to.be.true
        expect(mockSession.endSession.calledOnce).to.be.true
        expect(mockRt.isRevoked).to.be.true
        expect((mockRt.save as sinon.SinonStub).firstCall.args[0]).to.deep.equal({ session: mockSession })
        expect(updateManyStub.calledOnce).to.be.true
        expect(updateManyStub.firstCall.args[2]).to.deep.equal({ session: mockSession })
      } finally {
        mockAppConfig.rsAvailable = 'false'
      }
    })

    it('throws NotFoundError when grant does not exist or is already revoked', async () => {
      const grantId = new Types.ObjectId().toString()
      sinon.stub(OAuthRefreshToken, 'findOne').resolves(null)
      sinon.stub(OAuthAccessToken, 'findOne').resolves(null)

      try {
        await service.revokeUserGrant(orgId, userId, grantId)
        expect.fail('should have thrown')
      } catch (err: unknown) {
        if (err instanceof Error) {
          expect(err).to.be.instanceOf(NotFoundError)
        } else {
          expect.fail('Thrown error is not an Error')
        }
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
        type: 'refresh' as const,
      }

      sinon.stub(OAuthRefreshToken, 'aggregate').resolves([
        {
          metadata: [{ total: 1 }],
          data: [mockRefreshToken],
        },
      ])

      sinon.stub(Users, 'find').returns(
        createUserQueryFixture([
          {
            _id: userObjId,
            email: 'user@example.com',
            fullName: 'Test User',
            isDeleted: false,
          },
        ]),
      )

      sinon.stub(OAuthApp, 'find').returns(
        createOAuthAppQueryFixture([
          {
            clientId,
            name: 'Coding Agent',
            isConfidential: false,
          },
        ]),
      )

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

    it('includes valid standalone access tokens without refresh tokens in admin listing', async () => {
      const userObjId = new Types.ObjectId()
      const atId = new Types.ObjectId()
      const mockAccessToken = {
        _id: atId,
        clientId: 'standalone-agent',
        userId: userObjId,
        orgId: new Types.ObjectId(orgId),
        scopes: ['read', 'write'],
        createdAt: new Date('2026-09-10T10:00:00Z'),
        expiresAt: new Date('2026-10-10T10:00:00Z'),
        lastUsedAt: new Date('2026-09-11T12:00:00Z'),
        type: 'access' as const,
      }

      sinon.stub(OAuthRefreshToken, 'aggregate').resolves([
        {
          metadata: [{ total: 1 }],
          data: [mockAccessToken],
        },
      ])

      sinon.stub(Users, 'find').returns(
        createUserQueryFixture([
          {
            _id: userObjId,
            email: 'standalone@example.com',
            fullName: 'Standalone User',
            isDeleted: false,
          },
        ]),
      )

      sinon.stub(OAuthApp, 'find').returns(
        createOAuthAppQueryFixture([
          {
            clientId: 'standalone-agent',
            name: 'Standalone Agent App',
            isConfidential: true,
          },
        ]),
      )

      sinon.stub(OAuthAccessToken, 'aggregate').resolves([])

      const result = await service.listAllGrants(orgId, 1, 10)

      expect(result.data).to.have.length(1)
      expect(result.data[0]).to.deep.include({
        id: atId.toString(),
        clientId: 'standalone-agent',
        appName: 'Standalone Agent App',
        userId: userObjId.toString(),
        ownerEmail: 'standalone@example.com',
        ownerFullName: 'Standalone User',
        isConfidential: true,
        lastUsedAt: mockAccessToken.lastUsedAt,
      })
      expect(result.pagination.total).to.equal(1)
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
        type: 'refresh' as const,
      }

      sinon.stub(OAuthRefreshToken, 'aggregate').resolves([
        {
          metadata: [{ total: 1 }],
          data: [mockRefreshToken],
        },
      ])

      sinon.stub(Users, 'find').returns(
        createUserQueryFixture([
          {
            _id: userObjId,
            email: 'departed@example.com',
            fullName: 'Departed User',
            isDeleted: true,
          },
        ]),
      )

      sinon.stub(OAuthApp, 'find').returns(createOAuthAppQueryFixture([]))
      sinon.stub(OAuthAccessToken, 'aggregate').resolves([])

      const result = await service.listAllGrants(orgId)
      expect(result.data[0].ownerDeleted).to.be.true
    })
  })

  describe('adminRevokeGrant', () => {
    it('allows admin to revoke any grant in the organization filtering by parentRefreshTokenId', async () => {
      const grantId = new Types.ObjectId().toString()
      const adminUserId = new Types.ObjectId().toString()
      const targetUserObjId = new Types.ObjectId()

      const mockRt = new OAuthRefreshToken({
        _id: new Types.ObjectId(grantId),
        userId: targetUserObjId,
        clientId,
        isRevoked: false,
      })
      sinon.stub(mockRt, 'save').resolves()

      sinon
        .stub(OAuthRefreshToken, 'findOne')
        .resolves(mockRt)
      const updateManyStub = sinon
        .stub(OAuthAccessToken, 'updateMany')
        .resolves()

      await service.adminRevokeGrant(
        orgId,
        adminUserId,
        grantId,
        'incident response',
      )

      expect(mockRt.isRevoked).to.be.true
      expect((mockRt.save as sinon.SinonStub).calledOnce).to.be.true
      expect(updateManyStub.calledOnce).to.be.true
      const filterArg = updateManyStub.firstCall.args[0]
      expect(filterArg).to.have.property('parentRefreshTokenId')
    })

    it('revokes refresh token and associated access tokens within a transaction when REPLICA_SET_AVAILABLE is true', async () => {
      mockAppConfig.rsAvailable = 'true'
      try {
        const mockSession = {
          withTransaction: sinon.stub().callsFake(async (fn: () => Promise<void>) => {
            await fn()
          }),
          endSession: sinon.stub().resolves(),
        }
        sinon.stub(mongoose, 'startSession').resolves(mockSession as any)

        const grantId = new Types.ObjectId().toString()
        const adminUserId = new Types.ObjectId().toString()
        const targetUserObjId = new Types.ObjectId()

        const mockRt = new OAuthRefreshToken({
          _id: new Types.ObjectId(grantId),
          userId: targetUserObjId,
          clientId,
          isRevoked: false,
        })
        sinon.stub(mockRt, 'save').resolves()
        sinon.stub(OAuthRefreshToken, 'findOne').resolves(mockRt)
        const updateManyStub = sinon
          .stub(OAuthAccessToken, 'updateMany')
          .resolves()

        await service.adminRevokeGrant(
          orgId,
          adminUserId,
          grantId,
          'incident response',
        )

        expect(mockSession.withTransaction.calledOnce).to.be.true
        expect(mockSession.endSession.calledOnce).to.be.true
        expect(mockRt.isRevoked).to.be.true
        expect((mockRt.save as sinon.SinonStub).firstCall.args[0]).to.deep.equal({ session: mockSession })
        expect(updateManyStub.calledOnce).to.be.true
        expect(updateManyStub.firstCall.args[2]).to.deep.equal({ session: mockSession })
      } finally {
        mockAppConfig.rsAvailable = 'false'
      }
    })

    it('throws NotFoundError on invalid grantId', async () => {
      try {
        await service.adminRevokeGrant(orgId, userId, 'bad-id')
        expect.fail('should have thrown')
      } catch (err: unknown) {
        if (err instanceof Error) {
          expect(err).to.be.instanceOf(NotFoundError)
        } else {
          expect.fail('Thrown error is not an Error')
        }
      }
    })
  })
})
