import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { PatService } from '../../../../src/modules/oauth_provider/services/pat.service'
import { OAuthApp, OAuthAppStatus } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { ScopeValidatorService } from '../../../../src/modules/oauth_provider/services/scope.validator.service'
import { DefaultMcpScopes } from '../../../../src/modules/oauth_provider/config/scopes.config'
import { InvalidScopeError } from '../../../../src/libs/errors/oauth.errors'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { createMockLogger } from '../../../helpers/mock-logger'

const SECONDS_PER_DAY = 86400

describe('PatService', () => {
  let service: PatService
  let mockLogger: any
  let mockEncryptionService: any
  let mockConfigService: any
  let mockOAuthTokenService: any
  // Real ScopeValidatorService — no external deps, and this is the one
  // place PAT scope authorization is actually enforced.
  let scopeValidatorService: ScopeValidatorService

  const orgId = new Types.ObjectId().toString()
  const userId = new Types.ObjectId().toString()
  const patClientId = `pat-system:${orgId}`

  beforeEach(() => {
    mockLogger = createMockLogger()
    mockEncryptionService = {
      encrypt: sinon.stub().returns('encrypted-secret'),
    }
    mockConfigService = {
      getMcpScopes: sinon.stub().resolves(DefaultMcpScopes),
    }
    mockOAuthTokenService = {
      generateTokens: sinon.stub().resolves({
        accessToken: 'raw-token',
        accessTokenId: new Types.ObjectId().toString(),
        tokenType: 'Bearer',
        expiresIn: 90 * SECONDS_PER_DAY,
        scope: DefaultMcpScopes.join(' '),
      }),
      listAccessTokensForUser: sinon.stub().resolves([]),
      revokeAccessTokenById: sinon.stub().resolves(true),
      listAccessTokensForClientPaginated: sinon.stub().resolves({ tokens: [], total: 0 }),
      revokeAccessTokenByIdForClient: sinon.stub().resolves(true),
    }
    scopeValidatorService = new ScopeValidatorService()

    service = new PatService(
      mockLogger,
      mockEncryptionService,
      mockConfigService,
      mockOAuthTokenService,
      scopeValidatorService,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('getDefaultScopes', () => {
    it('delegates to ConfigService.getMcpScopes', async () => {
      const scopes = await service.getDefaultScopes()
      expect(scopes).to.deep.equal(DefaultMcpScopes)
      expect(mockConfigService.getMcpScopes.calledOnce).to.be.true
    })
  })

  describe('createToken', () => {
    const existingApp = {
      clientId: patClientId,
      accessTokenLifetime: 3600,
      createdBy: new Types.ObjectId(),
    } as any

    it('defaults to the full MCP scope set when no scopes are requested', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(existingApp)

      await service.createToken(orgId, userId, 'Test User', { name: 'my token' })

      const scopesArg = mockOAuthTokenService.generateTokens.firstCall.args[3]
      expect(scopesArg).to.deep.equal(DefaultMcpScopes)
    })

    it('throws InvalidScopeError when a requested scope is not in the MCP scope set', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(existingApp)

      try {
        await service.createToken(orgId, userId, 'Test User', {
          name: 'my token',
          scopes: ['org:admin'],
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidScopeError)
      }
      expect(mockOAuthTokenService.generateTokens.called).to.be.false
    })

    it('creates the org PAT app with a deterministic clientId when none exists yet', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      const createStub = sinon.stub(OAuthApp, 'create').resolves(existingApp)

      await service.createToken(orgId, userId, 'Test User', { name: 'my token' })

      expect(createStub.calledOnce).to.be.true
      expect(createStub.firstCall.args[0]).to.include({ clientId: patClientId })
      expect(mockEncryptionService.encrypt.calledOnce).to.be.true
    })

    it('recovers from a concurrent create race by re-fetching the winner app', async () => {
      const findOneStub = sinon.stub(OAuthApp, 'findOne')
      findOneStub.onCall(0).resolves(null)
      findOneStub.onCall(1).resolves(existingApp)
      sinon.stub(OAuthApp, 'create').rejects(new Error('E11000 duplicate key'))

      const result = await service.createToken(orgId, userId, 'Test User', { name: 'my token' })

      expect(result.accessToken).to.equal('phpat_raw-token')
      expect(findOneStub.callCount).to.equal(2)
    })

    it('propagates the create error when no app exists after a failed create (not a race)', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      sinon.stub(OAuthApp, 'create').rejects(new Error('some other db error'))

      try {
        await service.createToken(orgId, userId, 'Test User', { name: 'my token' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('some other db error')
      }
    })

    const expiryCases: Array<[30 | 90 | 365 | 'never' | undefined, number]> = [
      [30, 30 * SECONDS_PER_DAY],
      [90, 90 * SECONDS_PER_DAY],
      [365, 365 * SECONDS_PER_DAY],
      ['never', 365 * 100 * SECONDS_PER_DAY],
      [undefined, 30 * SECONDS_PER_DAY],
    ]
    for (const [expiryDays, expectedSeconds] of expiryCases) {
      it(`resolves expiryDays=${expiryDays} to a ${expectedSeconds}s lifetime`, async () => {
        sinon.stub(OAuthApp, 'findOne').resolves(existingApp)

        await service.createToken(orgId, userId, 'Test User', {
          name: 'my token',
          expiryDays,
        })

        const opts = mockOAuthTokenService.generateTokens.firstCall.args[7]
        expect(opts.accessTokenLifetimeOverrideSeconds).to.equal(expectedSeconds)
      })
    }

    it('always mints without a refresh token', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(existingApp)

      await service.createToken(orgId, userId, 'Test User', { name: 'my token' })

      expect(mockOAuthTokenService.generateTokens.firstCall.args[4]).to.equal(false)
    })

    it('returns a PatWithSecret whose id matches the minted accessTokenId', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(existingApp)
      const accessTokenId = new Types.ObjectId().toString()
      mockOAuthTokenService.generateTokens.resolves({
        accessToken: 'raw-token-2',
        accessTokenId,
        tokenType: 'Bearer',
        expiresIn: 90 * SECONDS_PER_DAY,
        scope: DefaultMcpScopes.join(' '),
      })

      const result = await service.createToken(orgId, userId, 'Test User', { name: 'my token' })

      expect(result.id).to.equal(accessTokenId)
      expect(result.accessToken).to.equal('phpat_raw-token-2')
      expect(result.name).to.equal('my token')
    })

    it('prefixes the returned access token with phpat_', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(existingApp)

      const result = await service.createToken(orgId, userId, 'Test User', { name: 'my token' })

      expect(result.accessToken).to.equal('phpat_raw-token')
      expect(result.accessToken.startsWith('phpat_')).to.be.true
    })
  })

  describe('listTokens', () => {
    it('returns an empty list without querying tokens when the org has no PAT app yet', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      const tokens = await service.listTokens(orgId, userId)

      expect(tokens).to.deep.equal([])
      expect(mockOAuthTokenService.listAccessTokensForUser.called).to.be.false
    })

    it('maps TokenListItem[] to PatListItem[], defaulting an unnamed token', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      const now = new Date()
      mockOAuthTokenService.listAccessTokensForUser.resolves([
        {
          id: 'tok-1',
          tokenType: 'access',
          scopes: ['kb:read'],
          createdAt: now,
          expiresAt: now,
          isRevoked: false,
          name: 'named token',
          lastUsedAt: now,
        },
        {
          id: 'tok-2',
          tokenType: 'access',
          scopes: ['kb:read'],
          createdAt: now,
          expiresAt: now,
          isRevoked: false,
        },
      ])

      const tokens = await service.listTokens(orgId, userId)

      expect(tokens).to.have.lengthOf(2)
      expect(tokens[0].name).to.equal('named token')
      expect(tokens[1].name).to.equal('(unnamed token)')
      expect(mockOAuthTokenService.listAccessTokensForUser.firstCall.args).to.deep.equal([
        patClientId,
        userId,
      ])
    })
  })

  describe('revokeToken', () => {
    it('throws NotFoundError when the org has no PAT app yet', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.revokeToken(orgId, userId, 'tok-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('throws NotFoundError when revokeAccessTokenById finds nothing to revoke', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      mockOAuthTokenService.revokeAccessTokenById.resolves(false)

      try {
        await service.revokeToken(orgId, userId, 'tok-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('revokes and logs on success', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      mockOAuthTokenService.revokeAccessTokenById.resolves(true)

      await service.revokeToken(orgId, userId, 'tok-1', 'no longer needed')

      expect(mockOAuthTokenService.revokeAccessTokenById.firstCall.args).to.deep.equal([
        'tok-1',
        patClientId,
        userId,
        userId,
        'no longer needed',
      ])
      expect(mockLogger.info.called).to.be.true
    })
  })

  describe('listAllTokens', () => {
    function stubUsersFind(users: any[]) {
      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(users),
      } as any)
    }

    it('returns an empty page without querying tokens when the org has no PAT app yet', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      const result = await service.listAllTokens(orgId)

      expect(result).to.deep.equal({
        data: [],
        pagination: { page: 1, limit: 100, total: 0, totalPages: 0 },
      })
      expect(mockOAuthTokenService.listAccessTokensForClientPaginated.called).to.be.false
    })

    it('lists tokens across all users and attaches owner info', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      const now = new Date()
      const ownerId = new Types.ObjectId().toString()
      mockOAuthTokenService.listAccessTokensForClientPaginated.resolves({
        tokens: [
          {
            id: 'tok-1',
            tokenType: 'access',
            userId: ownerId,
            scopes: ['kb:read'],
            createdAt: now,
            expiresAt: now,
            isRevoked: false,
            name: 'named token',
            lastUsedAt: now,
          },
        ],
        total: 1,
      })
      stubUsersFind([
        { _id: new Types.ObjectId(ownerId), email: 'owner@example.com', fullName: 'Owner', isDeleted: false },
      ])

      const result = await service.listAllTokens(orgId)

      expect(result.data).to.have.lengthOf(1)
      expect(result.data[0]).to.include({
        id: 'tok-1',
        userId: ownerId,
        ownerEmail: 'owner@example.com',
        ownerFullName: 'Owner',
        ownerDeleted: false,
      })
      expect(result.pagination).to.deep.equal({ page: 1, limit: 100, total: 1, totalPages: 1 })
      expect(mockOAuthTokenService.listAccessTokensForClientPaginated.firstCall.args).to.deep.equal([
        patClientId,
        1,
        100,
      ])
    })

    it('passes through the requested page/limit', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      stubUsersFind([])

      await service.listAllTokens(orgId, 2, 25)

      expect(mockOAuthTokenService.listAccessTokensForClientPaginated.firstCall.args).to.deep.equal([
        patClientId,
        2,
        25,
      ])
    })

    it('marks a token owned by a deleted user as ownerDeleted, keeping it in the list', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      const now = new Date()
      const deletedOwnerId = new Types.ObjectId().toString()
      mockOAuthTokenService.listAccessTokensForClientPaginated.resolves({
        tokens: [
          {
            id: 'tok-1',
            tokenType: 'access',
            userId: deletedOwnerId,
            scopes: ['kb:read'],
            createdAt: now,
            expiresAt: now,
            isRevoked: false,
            name: 'named token',
          },
        ],
        total: 1,
      })
      // Deliberately still returned by Users.find — the fix is that
      // listAllTokens no longer filters isDeleted: false out of that
      // query, so a soft-deleted owner's name/email still resolve.
      stubUsersFind([
        {
          _id: new Types.ObjectId(deletedOwnerId),
          email: 'gone@example.com',
          fullName: 'Departed Employee',
          isDeleted: true,
        },
      ])

      const result = await service.listAllTokens(orgId)

      expect(result.data[0]).to.include({
        ownerEmail: 'gone@example.com',
        ownerFullName: 'Departed Employee',
        ownerDeleted: true,
      })
    })

    it('marks a token as ownerDeleted when the owner no longer resolves at all', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      const now = new Date()
      mockOAuthTokenService.listAccessTokensForClientPaginated.resolves({
        tokens: [
          {
            id: 'tok-1',
            tokenType: 'access',
            userId: new Types.ObjectId().toString(),
            scopes: ['kb:read'],
            createdAt: now,
            expiresAt: now,
            isRevoked: false,
          },
        ],
        total: 1,
      })
      stubUsersFind([])

      const result = await service.listAllTokens(orgId)

      expect(result.data[0].ownerDeleted).to.be.true
      expect(result.data[0].ownerEmail).to.be.undefined
    })
  })

  describe('adminRevokeToken', () => {
    it('throws NotFoundError when the org has no PAT app yet', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.adminRevokeToken(orgId, userId, 'tok-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('throws NotFoundError when revokeAccessTokenByIdForClient finds nothing to revoke', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      mockOAuthTokenService.revokeAccessTokenByIdForClient.resolves(false)

      try {
        await service.adminRevokeToken(orgId, userId, 'tok-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('revokes any user\'s token, not just the admin\'s own', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: patClientId } as any)
      mockOAuthTokenService.revokeAccessTokenByIdForClient.resolves(true)

      await service.adminRevokeToken(orgId, userId, 'tok-1', 'departed employee')

      expect(mockOAuthTokenService.revokeAccessTokenByIdForClient.firstCall.args).to.deep.equal([
        'tok-1',
        patClientId,
        userId,
        'departed employee',
      ])
      expect(mockLogger.info.called).to.be.true
    })
  })
})
