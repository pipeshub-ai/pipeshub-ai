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

      const result = await service.getAppById(fakeAppId, fakeOrgId, fakeUserId, true)
      expect(result.name).to.equal('Test')
    })

    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.getAppById(fakeAppId, fakeOrgId, fakeUserId, true)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
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

      const result = await service.listApps(fakeOrgId, fakeUserId, true, { page: 1, limit: 10 })
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

      await service.listApps(fakeOrgId, fakeUserId, true, { status: OAuthAppStatus.ACTIVE })
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

      await service.listApps(fakeOrgId, fakeUserId, true, { search: 'test' })
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

      const result = await service.listApps(fakeOrgId, fakeUserId, true, {})
      expect(result.pagination.page).to.equal(1)
      expect(result.pagination.limit).to.equal(20)
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

      await service.deleteApp(fakeAppId, fakeOrgId, fakeUserId, true)
      expect(mockApp.isDeleted).to.be.true
      expect(mockApp.status).to.equal(OAuthAppStatus.REVOKED)
      expect(mockApp.save.calledOnce).to.be.true
    })

    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.deleteApp(fakeAppId, fakeOrgId, fakeUserId, true)
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

      const result = await service.regenerateSecret(fakeAppId, fakeOrgId, fakeUserId, true)
      expect(result).to.have.property('clientSecret')
      expect(mockEncryptionService.encrypt.called).to.be.true
      expect(mockApp.save.calledOnce).to.be.true
    })

    it('should throw NotFoundError when app not found', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves(null)
      try {
        await service.regenerateSecret(fakeAppId, fakeOrgId, fakeUserId, true)
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

      await service.suspendApp(fakeAppId, fakeOrgId, fakeUserId, true)
      expect(mockApp.status).to.equal(OAuthAppStatus.SUSPENDED)
    })

    it('should throw BadRequestError when already suspended', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: new Types.ObjectId(fakeAppId),
        status: OAuthAppStatus.SUSPENDED,
      } as any)
      try {
        await service.suspendApp(fakeAppId, fakeOrgId, fakeUserId, true)
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

      await service.activateApp(fakeAppId, fakeOrgId, fakeUserId, true)
      expect(mockApp.status).to.equal(OAuthAppStatus.ACTIVE)
    })

    it('should throw BadRequestError when app is revoked', async () => {
      sinon.stub(OAuthApp, 'findOne').resolves({
        _id: new Types.ObjectId(fakeAppId),
        status: OAuthAppStatus.REVOKED,
      } as any)
      try {
        await service.activateApp(fakeAppId, fakeOrgId, fakeUserId, true)
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
        await service.activateApp(fakeAppId, fakeOrgId, fakeUserId, true)
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
