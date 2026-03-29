import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import {
  InvalidClientError,
  InvalidRedirectUriError,
} from '../../../../src/libs/errors/oauth.errors'
import { NotFoundError, BadRequestError } from '../../../../src/libs/errors/http.errors'
import { OAuthAppStatus, OAuthGrantType, OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service'
import { createMockLogger } from '../../../helpers/mock-logger'

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
        await service.getAppById(VALID_APP_ID, VALID_ORG_ID)
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

      const result = await service.listApps(VALID_ORG_ID, {})
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

      await service.listApps(VALID_ORG_ID, { status: OAuthAppStatus.ACTIVE })
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

      await service.listApps(VALID_ORG_ID, { search: 'test.app' })
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

      const result = await service.listApps(VALID_ORG_ID, { page: 1, limit: 10 })
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
        await service.updateApp(VALID_APP_ID, VALID_ORG_ID, { name: 'New Name' })
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

      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, { allowedScopes: ['user:read'] })
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
      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, { redirectUris: ['https://example.com/callback'] })
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
        await service.updateApp(VALID_APP_ID, VALID_ORG_ID, { allowedGrantTypes: ['invalid_grant'] })
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

      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, {
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

      await service.updateApp(VALID_APP_ID, VALID_ORG_ID, {
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
        await service.suspendApp(VALID_APP_ID, VALID_ORG_ID)
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
        await service.suspendApp(VALID_APP_ID, VALID_ORG_ID)
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
        await service.activateApp(VALID_APP_ID, VALID_ORG_ID)
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
        await service.activateApp(VALID_APP_ID, VALID_ORG_ID)
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
        await service.activateApp(VALID_APP_ID, VALID_ORG_ID)
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
        await service.regenerateSecret(VALID_APP_ID, VALID_ORG_ID)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })
})
