import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service'
import {
  OAuthApp,
  OAuthAppStatus,
  OAuthGrantType,
} from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import {
  InvalidClientError,
  InvalidRedirectUriError,
} from '../../../../src/libs/errors/oauth.errors'
import { NotFoundError, BadRequestError } from '../../../../src/libs/errors/http.errors'
import { createMockLogger } from '../../../helpers/mock-logger'

describe('OAuthAppService', () => {
  let service: OAuthAppService
  let mockLogger: any
  let mockEncryptionService: any
  let mockScopeValidatorService: any

  const fakeOrgId = new Types.ObjectId().toString()
  const fakeUserId = new Types.ObjectId().toString()
  const fakeAppId = new Types.ObjectId().toString()

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
    mockEncryptionService = {
      encrypt: sinon.stub().returns('encrypted-secret'),
      decrypt: sinon.stub().returns('decrypted-secret'),
    }
    mockScopeValidatorService = {
      validateRequestedScopes: sinon.stub(),
      getAllowedScopeNamesForRole: sinon.stub().returns(['org:read', 'user:read']),
    }
    service = new OAuthAppService(
      mockLogger,
      mockEncryptionService,
      mockScopeValidatorService,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('createApp', () => {
    it('should create an OAuth app and return with client secret', async () => {
      const mockApp = {
        _id: new Types.ObjectId(),
        slug: 'test-app',
        clientId: 'test-client-id',
        name: 'Test App',
        description: 'A test app',
        redirectUris: ['https://example.com/callback'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
        allowedScopes: ['org:read'],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
      }

      sinon.stub(OAuthApp, 'create').resolves(mockApp as any)

      const result = await service.createApp(fakeOrgId, fakeUserId, true, {
        name: 'Test App',
        description: 'A test app',
        allowedScopes: ['org:read'],
        redirectUris: ['https://example.com/callback'],
      })

      expect(result).to.have.property('clientSecret')
      expect(result).to.have.property('clientId')
      expect(result.name).to.equal('Test App')
      expect(mockScopeValidatorService.validateRequestedScopes.calledOnce).to.be.true
      expect(mockEncryptionService.encrypt.calledOnce).to.be.true
    })

    it('should use default grant types when not specified', async () => {
      const mockApp = {
        _id: new Types.ObjectId(),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: ['https://example.com/cb'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.REFRESH_TOKEN],
        allowedScopes: ['org:read'],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
      }

      const createStub = sinon.stub(OAuthApp, 'create').resolves(mockApp as any)

      await service.createApp(fakeOrgId, fakeUserId, true, {
        name: 'Test',
        allowedScopes: ['org:read'],
        redirectUris: ['https://example.com/cb'],
      })

      const createArgs = createStub.firstCall.args[0] as any
      expect(createArgs.allowedGrantTypes).to.deep.include(OAuthGrantType.AUTHORIZATION_CODE)
      expect(createArgs.allowedGrantTypes).to.deep.include(OAuthGrantType.REFRESH_TOKEN)
    })

    it('should throw BadRequestError for invalid grant type', async () => {
      try {
        await service.createApp(fakeOrgId, fakeUserId, true, {
          name: 'Test',
          allowedScopes: ['org:read'],
          allowedGrantTypes: ['invalid_grant' as any],
          redirectUris: ['https://example.com/cb'],
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should throw InvalidRedirectUriError for non-HTTPS redirect URI', async () => {
      try {
        await service.createApp(fakeOrgId, fakeUserId, true, {
          name: 'Test',
          allowedScopes: ['org:read'],
          redirectUris: ['http://example.com/callback'],
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
      }
    })

    it('should allow localhost redirect URIs', async () => {
      const mockApp = {
        _id: new Types.ObjectId(),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: ['http://localhost:3000/callback'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
        allowedScopes: ['org:read'],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
      }
      sinon.stub(OAuthApp, 'create').resolves(mockApp as any)

      const result = await service.createApp(fakeOrgId, fakeUserId, true, {
        name: 'Test',
        allowedScopes: ['org:read'],
        redirectUris: ['http://localhost:3000/callback'],
      })

      expect(result).to.have.property('clientId')
    })

    it('should throw InvalidRedirectUriError for URI with fragment', async () => {
      try {
        await service.createApp(fakeOrgId, fakeUserId, true, {
          name: 'Test',
          allowedScopes: ['org:read'],
          redirectUris: ['https://example.com/callback#fragment'],
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
      }
    })

    it('should throw InvalidRedirectUriError for invalid URL', async () => {
      try {
        await service.createApp(fakeOrgId, fakeUserId, true, {
          name: 'Test',
          allowedScopes: ['org:read'],
          redirectUris: ['not-a-valid-url'],
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
      }
    })

    it('should call getAllowedScopeNamesForRole(true) when creating as org admin', async () => {
      const mockApp = {
        _id: new Types.ObjectId(),
        slug: 't',
        clientId: 'cid',
        name: 'T',
        redirectUris: ['https://example.com/cb'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
        allowedScopes: ['org:read'],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
      }
      sinon.stub(OAuthApp, 'create').resolves(mockApp as any)

      await service.createApp(fakeOrgId, fakeUserId, true, {
        name: 'T',
        allowedScopes: ['org:read'],
        redirectUris: ['https://example.com/cb'],
      })

      expect(mockScopeValidatorService.getAllowedScopeNamesForRole.calledWith(true)).to.be.true
    })

    it('should call getAllowedScopeNamesForRole(false) when creating as non-admin member', async () => {
      const mockApp = {
        _id: new Types.ObjectId(),
        slug: 't',
        clientId: 'cid',
        name: 'T',
        redirectUris: ['https://example.com/cb'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
        allowedScopes: ['org:read'],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
      }
      sinon.stub(OAuthApp, 'create').resolves(mockApp as any)

      await service.createApp(fakeOrgId, fakeUserId, false, {
        name: 'T',
        allowedScopes: ['org:read'],
        redirectUris: ['https://example.com/cb'],
      })

      expect(mockScopeValidatorService.getAllowedScopeNamesForRole.calledWith(false)).to.be.true
    })
  })

  describe('getAppById', () => {
    it('should return app when found', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      const result = await service.getAppById(fakeAppId, fakeOrgId, fakeUserId)
      expect(result.name).to.equal('Test')
    })

    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.getAppById(fakeAppId, fakeOrgId, fakeUserId)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should include createdBy in findOne filter (creator-scoped access)', async () => {
      const findStub = sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.getAppById(fakeAppId, fakeOrgId, fakeUserId)
      } catch {
        // expected NotFoundError
      }
      const filter = findStub.firstCall.args[0] as Record<string, unknown>
      expect(filter.createdBy).to.deep.equal(new Types.ObjectId(fakeUserId))
    })

    it('should throw NotFoundError when app is not visible to caller (e.g. different creator in same org)', async () => {
      const callerUserId = new Types.ObjectId().toString()
      sinon.stub(OAuthApp, 'findOne').callsFake((filter: Record<string, unknown>) => {
        expect(filter.createdBy).to.deep.equal(new Types.ObjectId(callerUserId))
        return Promise.resolve(null)
      })
      try {
        await service.getAppById(fakeAppId, fakeOrgId, callerUserId)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
        expect((error as NotFoundError).message).to.equal('OAuth app not found')
      }
    })
  })

  describe('getAppByClientId', () => {
    it('should return app when found and active', async () => {
      const mockApp = {
        _id: new Types.ObjectId(),
        clientId: 'test-client-id',
        status: OAuthAppStatus.ACTIVE,
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      const result = await service.getAppByClientId('test-client-id')
      expect(result.clientId).to.equal('test-client-id')
    })

    it('should throw InvalidClientError when not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.getAppByClientId('nonexistent')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidClientError)
      }
    })

    it('should throw InvalidClientError when app is suspended', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: new Types.ObjectId(),
        clientId: 'cid',
        status: OAuthAppStatus.SUSPENDED,
      } as any)
      try {
        await service.getAppByClientId('cid')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidClientError)
        expect((error as InvalidClientError).message).to.include('suspended')
      }
    })
  })

  describe('listApps', () => {
    it('should return paginated results', async () => {
      const mockApps = [{
        _id: new Types.ObjectId(),
        slug: 'test',
        clientId: 'cid',
        name: 'App1',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
      }]

      const chainable = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(mockApps),
      }
      sinon.stub(OAuthApp, 'find').returns(chainable as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(1)

      const result = await service.listApps(fakeOrgId, fakeUserId, { page: 1, limit: 10 })
      expect(result.data).to.have.lengthOf(1)
      expect(result.pagination.total).to.equal(1)
      expect(result.pagination.page).to.equal(1)
    })

    it('should filter by status when provided', async () => {
      const chainable = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      }
      const findStub = sinon.stub(OAuthApp, 'find').returns(chainable as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      await service.listApps(fakeOrgId, fakeUserId, { status: OAuthAppStatus.ACTIVE })
      const filter = findStub.firstCall.args[0] as any
      expect(filter.status).to.deep.equal({ $eq: OAuthAppStatus.ACTIVE })
    })

    it('should support search query', async () => {
      const chainable = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      }
      const findStub = sinon.stub(OAuthApp, 'find').returns(chainable as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      await service.listApps(fakeOrgId, fakeUserId, { search: 'test' })
      const filter = findStub.firstCall.args[0] as any
      expect(filter.$or).to.be.an('array')
    })

    it('should use default pagination values', async () => {
      const chainable = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      }
      sinon.stub(OAuthApp, 'find').returns(chainable as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      const result = await service.listApps(fakeOrgId, fakeUserId, {})
      expect(result.pagination.page).to.equal(1)
      expect(result.pagination.limit).to.equal(20)
    })

    it('should restrict listApps to apps created by the user when not org admin', async () => {
      const chainable = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      }
      const findStub = sinon.stub(OAuthApp, 'find').returns(chainable as any)
      const countStub = sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      await service.listApps(fakeOrgId, fakeUserId, {})

      const expectedCreatedBy = new Types.ObjectId(fakeUserId)
      const findFilter = findStub.firstCall.args[0] as Record<string, unknown>
      const countFilter = countStub.firstCall.args[0] as Record<string, unknown>
      expect(findFilter.createdBy).to.deep.equal(expectedCreatedBy)
      expect(countFilter.createdBy).to.deep.equal(expectedCreatedBy)
    })

    it('should set createdBy on listApps filter when caller is org admin (same as members)', async () => {
      const chainable = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      }
      const findStub = sinon.stub(OAuthApp, 'find').returns(chainable as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      await service.listApps(fakeOrgId, fakeUserId, {})

      const findFilter = findStub.firstCall.args[0] as Record<string, unknown>
      expect(findFilter.createdBy).to.deep.equal(new Types.ObjectId(fakeUserId))
    })
  })

  describe('updateApp', () => {
    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.updateApp(fakeAppId, fakeOrgId, fakeUserId, true, { name: 'Updated' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should update app fields and save', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        name: 'Old Name',
        description: 'Old Desc',
        redirectUris: [],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
        allowedScopes: ['org:read'],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      const result = await service.updateApp(fakeAppId, fakeOrgId, fakeUserId, true, { name: 'New Name' })
      expect(mockApp.name).to.equal('New Name')
      expect(mockApp.save.calledOnce).to.be.true
    })

    it('should validate scopes when provided', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.updateApp(fakeAppId, fakeOrgId, fakeUserId, true, { allowedScopes: ['org:read'] })
      expect(mockScopeValidatorService.validateRequestedScopes.calledOnce).to.be.true
    })

    it('should pass isAdmin to scope allow-list when updating allowedScopes', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.updateApp(fakeAppId, fakeOrgId, fakeUserId, false, { allowedScopes: ['org:read'] })
      expect(mockScopeValidatorService.getAllowedScopeNamesForRole.calledWith(false)).to.be.true
    })

    it('should apply createdBy to findOne when non-admin updates an app', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
      const findStub = sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.updateApp(fakeAppId, fakeOrgId, fakeUserId, false, { name: 'X' })

      const filter = findStub.firstCall.args[0] as Record<string, unknown>
      expect(filter.createdBy).to.deep.equal(new Types.ObjectId(fakeUserId))
    })
  })

  describe('deleteApp', () => {
    it('should soft-delete the app', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        isDeleted: false,
        status: OAuthAppStatus.ACTIVE,
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.deleteApp(fakeAppId, fakeOrgId, fakeUserId)
      expect(mockApp.isDeleted).to.be.true
      expect(mockApp.deletedBy).to.deep.equal(new Types.ObjectId(fakeUserId))
      expect(mockApp.status).to.equal(OAuthAppStatus.REVOKED)
      expect(mockApp.save.calledOnce).to.be.true
    })

    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.deleteApp(fakeAppId, fakeOrgId, fakeUserId)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('regenerateSecret', () => {
    it('should regenerate and return new secret', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        clientSecretEncrypted: 'old-encrypted',
        name: 'Test',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      const result = await service.regenerateSecret(fakeAppId, fakeOrgId, fakeUserId)
      expect(result).to.have.property('clientSecret')
      expect(mockEncryptionService.encrypt.called).to.be.true
      expect(mockApp.save.calledOnce).to.be.true
    })

    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.regenerateSecret(fakeAppId, fakeOrgId, fakeUserId)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('suspendApp', () => {
    it('should suspend an active app', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.ACTIVE,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.suspendApp(fakeAppId, fakeOrgId, fakeUserId)
      expect(mockApp.status).to.equal(OAuthAppStatus.SUSPENDED)
    })

    it('should throw BadRequestError when already suspended', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: new Types.ObjectId(fakeAppId),
        status: OAuthAppStatus.SUSPENDED,
      } as any)
      try {
        await service.suspendApp(fakeAppId, fakeOrgId, fakeUserId)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })
  })

  describe('activateApp', () => {
    it('should activate a suspended app', async () => {
      const mockApp = {
        _id: new Types.ObjectId(fakeAppId),
        slug: 'test',
        clientId: 'cid',
        name: 'Test',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        status: OAuthAppStatus.SUSPENDED,
        isConfidential: true,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        createdAt: new Date(),
        updatedAt: new Date(),
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.activateApp(fakeAppId, fakeOrgId, fakeUserId)
      expect(mockApp.status).to.equal(OAuthAppStatus.ACTIVE)
    })

    it('should throw BadRequestError when app is revoked', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: new Types.ObjectId(fakeAppId),
        status: OAuthAppStatus.REVOKED,
      } as any)
      try {
        await service.activateApp(fakeAppId, fakeOrgId, fakeUserId)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should throw BadRequestError when already active', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: new Types.ObjectId(fakeAppId),
        status: OAuthAppStatus.ACTIVE,
      } as any)
      try {
        await service.activateApp(fakeAppId, fakeOrgId, fakeUserId)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })
  })

  describe('verifyClientCredentials', () => {
    it('should return app when credentials match', async () => {
      const secret = 'test-secret'
      const mockApp = {
        _id: new Types.ObjectId(),
        clientId: 'cid',
        clientSecretEncrypted: 'encrypted',
        status: OAuthAppStatus.ACTIVE,
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)
      mockEncryptionService.decrypt.returns(secret)

      const result = await service.verifyClientCredentials('cid', secret)
      expect(result.clientId).to.equal('cid')
    })

    it('should throw InvalidClientError when credentials do not match', async () => {
      const mockApp = {
        _id: new Types.ObjectId(),
        clientId: 'cid',
        clientSecretEncrypted: 'encrypted',
        status: OAuthAppStatus.ACTIVE,
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)
      mockEncryptionService.decrypt.returns('stored-secret')

      try {
        await service.verifyClientCredentials('cid', 'wrong-secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidClientError)
      }
    })
  })

  describe('validateRedirectUriForApp', () => {
    it('should not throw for valid redirect URI', () => {
      const app = { redirectUris: ['https://example.com/cb'] } as any
      expect(() => service.validateRedirectUriForApp(app, 'https://example.com/cb')).to.not.throw()
    })

    it('should throw InvalidRedirectUriError for unregistered URI', () => {
      const app = { redirectUris: ['https://example.com/cb'] } as any
      try {
        service.validateRedirectUriForApp(app, 'https://other.com/cb')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
      }
    })
  })

  describe('isGrantTypeAllowed', () => {
    it('should return true when grant type is allowed', () => {
      const app = { allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE] } as any
      expect(service.isGrantTypeAllowed(app, 'authorization_code')).to.be.true
    })

    it('should return false when grant type is not allowed', () => {
      const app = { allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE] } as any
      expect(service.isGrantTypeAllowed(app, 'client_credentials')).to.be.false
    })
  })
})

{
const VALID_APP_ID = new Types.ObjectId().toString()
const VALID_ORG_ID = new Types.ObjectId().toString()
const VALID_USER_ID = new Types.ObjectId().toString()

describe('OAuthAppService - branch coverage', () => {
  let service: OAuthAppService
  let mockLogger: any
  let mockEncryptionService: any
  let mockScopeValidatorService: any

  beforeEach(() => {
    mockLogger = createMockLogger()
    mockEncryptionService = {
      encrypt: sinon.stub().returns('encrypted-secret'),
      decrypt: sinon.stub().returns('decrypted-secret'),
    }
    mockScopeValidatorService = {
      validateRequestedScopes: sinon.stub(),
      getAllowedScopeNamesForRole: sinon.stub().returns(['org:read', 'user:read']),
    }
    service = new OAuthAppService(
      mockLogger as any,
      mockEncryptionService as any,
      mockScopeValidatorService as any,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  // =========================================================================
  // getAppByClientId - branches
  // =========================================================================
  describe('getAppByClientId', () => {
    it('should throw InvalidClientError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.getAppByClientId('nonexistent-client-id')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidClientError)
        expect((error as Error).message).to.equal('Invalid client_id')
      }
    })

    it('should throw InvalidClientError when app is not active', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        status: OAuthAppStatus.SUSPENDED,
        clientId: 'client-1',
      } as any)

      try {
        await service.getAppByClientId('client-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidClientError)
        expect((error as Error).message).to.include('suspended')
      }
    })

    it('should throw InvalidClientError when app is revoked', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        status: OAuthAppStatus.REVOKED,
        clientId: 'client-1',
      } as any)

      try {
        await service.getAppByClientId('client-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidClientError)
      }
    })

    it('should return app when active', async () => {
      const mockApp = {
        status: OAuthAppStatus.ACTIVE,
        clientId: 'client-1',
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      const result = await service.getAppByClientId('client-1')
      expect(result).to.equal(mockApp)
    })
  })

  // =========================================================================
  // getAppById
  // =========================================================================
  describe('getAppById', () => {
    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.getAppById(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  // =========================================================================
  // listApps - query branches
  // =========================================================================
  describe('listApps', () => {
    it('should use default page and limit when not provided', async () => {
      const mockExec = sinon.stub().resolves([])
      const mockLimit = sinon.stub().returns({ exec: mockExec })
      const mockSkip = sinon.stub().returns({ limit: mockLimit })
      const mockSort = sinon.stub().returns({ skip: mockSkip })
      sinon.stub(OAuthApp, 'find').returns({ sort: mockSort } as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      const result = await service.listApps(VALID_ORG_ID, VALID_USER_ID, {})
      expect(result.pagination.page).to.equal(1)
      expect(result.pagination.limit).to.equal(20)
    })

    it('should apply status filter when provided', async () => {
      const mockExec = sinon.stub().resolves([])
      const mockLimit = sinon.stub().returns({ exec: mockExec })
      const mockSkip = sinon.stub().returns({ limit: mockLimit })
      const mockSort = sinon.stub().returns({ skip: mockSkip })
      const findStub = sinon.stub(OAuthApp, 'find').returns({ sort: mockSort } as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      await service.listApps(VALID_ORG_ID, VALID_USER_ID, { status: OAuthAppStatus.ACTIVE })
      // Check the filter contains status
      const filterArg = findStub.firstCall.args[0]
      expect(filterArg.status).to.deep.equal({ $eq: OAuthAppStatus.ACTIVE })
    })

    it('should apply search filter with regex escaping when provided', async () => {
      const mockExec = sinon.stub().resolves([])
      const mockLimit = sinon.stub().returns({ exec: mockExec })
      const mockSkip = sinon.stub().returns({ limit: mockLimit })
      const mockSort = sinon.stub().returns({ skip: mockSkip })
      const findStub = sinon.stub(OAuthApp, 'find').returns({ sort: mockSort } as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(0)

      await service.listApps(VALID_ORG_ID, VALID_USER_ID, { search: 'test.app' })
      // Check the filter contains $or with regex
      const filterArg = findStub.firstCall.args[0]
      expect(filterArg.$or).to.exist
      expect(filterArg.$or).to.have.length(2)
    })

    it('should calculate totalPages correctly', async () => {
      const mockExec = sinon.stub().resolves([])
      const mockLimit = sinon.stub().returns({ exec: mockExec })
      const mockSkip = sinon.stub().returns({ limit: mockLimit })
      const mockSort = sinon.stub().returns({ skip: mockSkip })
      sinon.stub(OAuthApp, 'find').returns({ sort: mockSort } as any)
      sinon.stub(OAuthApp, 'countDocuments').resolves(45)

      const result = await service.listApps(VALID_ORG_ID, VALID_USER_ID, { page: 1, limit: 10 })
      expect(result.pagination.totalPages).to.equal(5)
      expect(result.pagination.total).to.equal(45)
    })
  })

  // =========================================================================
  // updateApp - conditional field updates
  // =========================================================================
  describe('updateApp', () => {
    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.updateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID, true, { name: 'New Name' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should validate scopes when allowedScopes is provided', async () => {
      const mockApp = {
        _id: { toString: () => 'app-1' },
        name: 'Old',
        save: sinon.stub().resolves(),
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID, true, { allowedScopes: ['user:read'] })
      expect(mockScopeValidatorService.validateRequestedScopes.calledOnce).to.be.true
    })

    it('should validate redirectUris when provided', async () => {
      const mockApp = {
        _id: { toString: () => 'app-1' },
        name: 'Old',
        save: sinon.stub().resolves(),
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      // Valid https redirect URI
      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID, true, { redirectUris: ['https://example.com/callback'] })
    })

    it('should validate grantTypes when allowedGrantTypes is provided', async () => {
      const mockApp = {
        _id: { toString: () => 'app-1' },
        name: 'Old',
        save: sinon.stub().resolves(),
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      // Invalid grant type
      try {
        await service.updateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID, true, { allowedGrantTypes: ['invalid_grant'] })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should update all optional fields when provided', async () => {
      const mockApp = {
        _id: { toString: () => 'app-1' },
        name: 'Old',
        description: 'Old desc',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
        homepageUrl: undefined,
        privacyPolicyUrl: undefined,
        termsOfServiceUrl: undefined,
        accessTokenLifetime: 3600,
        refreshTokenLifetime: 2592000,
        save: sinon.stub().resolves(),
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID, true, {
        name: 'New',
        description: 'New desc',
        homepageUrl: 'https://home.com',
        privacyPolicyUrl: 'https://privacy.com',
        termsOfServiceUrl: 'https://terms.com',
        accessTokenLifetime: 7200,
        refreshTokenLifetime: 5184000,
      })

      expect(mockApp.name).to.equal('New')
      expect(mockApp.description).to.equal('New desc')
      expect(mockApp.homepageUrl).to.equal('https://home.com')
      expect(mockApp.accessTokenLifetime).to.equal(7200)
    })

    it('should handle null homepageUrl (set to undefined)', async () => {
      const mockApp = {
        _id: { toString: () => 'app-1' },
        name: 'Old',
        homepageUrl: 'https://old.com',
        privacyPolicyUrl: 'https://old-privacy.com',
        termsOfServiceUrl: 'https://old-terms.com',
        save: sinon.stub().resolves(),
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: [],
      }
      sinon.stub(OAuthApp, 'findOne').resolves(mockApp as any)

      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID, true, {
        homepageUrl: null,
        privacyPolicyUrl: null,
        termsOfServiceUrl: null,
      })

      expect(mockApp.homepageUrl).to.be.undefined
      expect(mockApp.privacyPolicyUrl).to.be.undefined
      expect(mockApp.termsOfServiceUrl).to.be.undefined
    })
  })

  // =========================================================================
  // suspendApp - branches
  // =========================================================================
  describe('suspendApp', () => {
    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.suspendApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw BadRequestError when already suspended', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: { toString: () => 'app-1' },
        status: OAuthAppStatus.SUSPENDED,
        save: sinon.stub().resolves(),
      } as any)

      try {
        await service.suspendApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect((error as Error).message).to.include('already suspended')
      }
    })
  })

  // =========================================================================
  // activateApp - branches
  // =========================================================================
  describe('activateApp', () => {
    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.activateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw BadRequestError when app is revoked', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: { toString: () => 'app-1' },
        status: OAuthAppStatus.REVOKED,
        save: sinon.stub().resolves(),
      } as any)

      try {
        await service.activateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect((error as Error).message).to.include('Cannot activate a revoked app')
      }
    })

    it('should throw BadRequestError when already active', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: { toString: () => 'app-1' },
        status: OAuthAppStatus.ACTIVE,
        save: sinon.stub().resolves(),
      } as any)

      try {
        await service.activateApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect((error as Error).message).to.include('already active')
      }
    })
  })

  // =========================================================================
  // validateRedirectUris - branches
  // =========================================================================
  describe('validateRedirectUris (private)', () => {
    it('should accept localhost HTTP', () => {
      // Valid: http://localhost/callback
      expect(() => {
        ;(service as any).validateRedirectUris(['http://localhost/callback'])
      }).to.not.throw()
    })

    it('should accept 127.0.0.1 HTTP', () => {
      expect(() => {
        ;(service as any).validateRedirectUris(['http://127.0.0.1/callback'])
      }).to.not.throw()
    })

    it('should accept HTTPS URLs', () => {
      expect(() => {
        ;(service as any).validateRedirectUris(['https://example.com/callback'])
      }).to.not.throw()
    })

    it('should reject non-HTTPS non-localhost URLs', () => {
      try {
        ;(service as any).validateRedirectUris(['http://example.com/callback'])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
        expect((error as Error).message).to.include('must use HTTPS')
      }
    })

    it('should reject URLs with fragments', () => {
      try {
        ;(service as any).validateRedirectUris(['https://example.com/callback#fragment'])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
        expect((error as Error).message).to.include('fragment')
      }
    })

    it('should reject completely invalid URIs', () => {
      try {
        ;(service as any).validateRedirectUris(['not-a-url'])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
        expect((error as Error).message).to.include('Invalid redirect URI')
      }
    })

    it('should accept whitelisted custom redirect URIs', () => {
      // This tests the ALLOWED_CUSTOM_REDIRECT_URIS continue branch
      expect(() => {
        ;(service as any).validateRedirectUris(['cursor://anysphere.cursor-mcp/oauth/callback'])
      }).to.not.throw()
    })
  })

  // =========================================================================
  // validateGrantTypes - branches
  // =========================================================================
  describe('validateGrantTypes (private)', () => {
    it('should accept valid grant types', () => {
      expect(() => {
        ;(service as any).validateGrantTypes([OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.REFRESH_TOKEN])
      }).to.not.throw()
    })

    it('should reject invalid grant type', () => {
      try {
        ;(service as any).validateGrantTypes(['invalid_grant'])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect((error as Error).message).to.include('Invalid grant type')
      }
    })
  })

  // =========================================================================
  // validateRedirectUriForApp
  // =========================================================================
  describe('validateRedirectUriForApp', () => {
    it('should throw InvalidRedirectUriError when URI not in app redirectUris', () => {
      const app = { redirectUris: ['https://a.com/callback'] } as any

      try {
        service.validateRedirectUriForApp(app, 'https://b.com/callback')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidRedirectUriError)
      }
    })

    it('should pass when URI is in app redirectUris', () => {
      const app = { redirectUris: ['https://a.com/callback'] } as any
      expect(() => service.validateRedirectUriForApp(app, 'https://a.com/callback')).to.not.throw()
    })
  })

  // =========================================================================
  // isGrantTypeAllowed
  // =========================================================================
  describe('isGrantTypeAllowed', () => {
    it('should return true when grant type is allowed', () => {
      const app = { allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE] } as any
      expect(service.isGrantTypeAllowed(app, OAuthGrantType.AUTHORIZATION_CODE)).to.be.true
    })

    it('should return false when grant type is not allowed', () => {
      const app = { allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE] } as any
      expect(service.isGrantTypeAllowed(app, 'client_credentials')).to.be.false
    })
  })

  // =========================================================================
  // deleteApp
  // =========================================================================
  describe('deleteApp', () => {
    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.deleteApp(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  // =========================================================================
  // regenerateSecret
  // =========================================================================
  describe('regenerateSecret', () => {
    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)

      try {
        await service.regenerateSecret(VALID_APP_ID, VALID_ORG_ID, VALID_USER_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })
})
}
