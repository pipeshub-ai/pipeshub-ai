import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OAuthProviderContainer } from '../../../../src/modules/oauth_provider/container/oauth.provider.container'
import * as jwtConfigModule from '../../../../src/libs/utils/jwtConfig'

describe('OAuthProviderContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (OAuthProviderContainer as any).instance
  })

  afterEach(() => {
    (OAuthProviderContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings', async () => {
      sinon.stub(jwtConfigModule, 'getJwtConfig').resolves({
        privateKey: 'test-private-key',
        publicKey: 'test-public-key',
        algorithm: 'RS256',
        keyId: 'test-key-id',
      } as any)

      const { ConfigService } = require('../../../../src/modules/tokens_manager/services/cm.service')
      sinon.stub(ConfigService, 'getInstance').returns({
        getConfig: sinon.stub().resolves({}),
      })

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        oauthIssuer: 'http://localhost:3001/api/v1/oauth-provider',
        authBackend: 'http://localhost:3001',
      } as any

      const container = await OAuthProviderContainer.initialize(cmConfig as any, appConfig)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('JWT_SECRET')).to.be.true
      expect(container.isBound('SCOPED_JWT_SECRET')).to.be.true
      expect(container.isBound('OAUTH_ISSUER')).to.be.true
      expect(container.isBound('EncryptionService')).to.be.true
      expect(container.isBound('AuthTokenService')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true
      expect(container.isBound('ScopeValidatorService')).to.be.true
      expect(container.isBound('AuthorizationCodeService')).to.be.true
      expect(container.isBound('OAuthAppService')).to.be.true
      expect(container.isBound('JwtConfig')).to.be.true
      expect(container.isBound('OAuthTokenService')).to.be.true
      expect(container.isBound('OAuthAuthMiddleware')).to.be.true
      expect(container.isBound('OAuthAppController')).to.be.true
      expect(container.isBound('OAuthProviderController')).to.be.true
      expect(container.isBound('OIDCProviderController')).to.be.true

      const instance = OAuthProviderContainer.getInstance()
      expect(instance).to.equal(container)

      ;(OAuthProviderContainer as any).instance = null
    })

    it('should use authBackend as oauth issuer when oauthIssuer is not set', async () => {
      sinon.stub(jwtConfigModule, 'getJwtConfig').resolves({
        privateKey: 'test-private-key',
        publicKey: 'test-public-key',
        algorithm: 'RS256',
        keyId: 'test-key-id',
      } as any)

      const { ConfigService } = require('../../../../src/modules/tokens_manager/services/cm.service')
      sinon.stub(ConfigService, 'getInstance').returns({
        getConfig: sinon.stub().resolves({}),
      })

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        oauthIssuer: '',
        authBackend: 'http://localhost:3001',
      } as any

      const container = await OAuthProviderContainer.initialize(cmConfig as any, appConfig)

      const issuer = container.get<string>('OAUTH_ISSUER')
      expect(issuer).to.equal('http://localhost:3001/api/v1/oauth-provider')

      ;(OAuthProviderContainer as any).instance = null
    })

    it('should throw when getJwtConfig fails', async () => {
      sinon.stub(jwtConfigModule, 'getJwtConfig').rejects(new Error('JWT config unavailable'))

      const { ConfigService } = require('../../../../src/modules/tokens_manager/services/cm.service')
      sinon.stub(ConfigService, 'getInstance').returns({
        getConfig: sinon.stub().resolves({}),
      })

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        oauthIssuer: 'http://localhost:3001/api/v1/oauth-provider',
        authBackend: 'http://localhost:3001',
      } as any

      try {
        await OAuthProviderContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('JWT config unavailable')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should set instance to null after dispose', async () => {
      const mockContainer = {}
      ;(OAuthProviderContainer as any).instance = mockContainer

      await OAuthProviderContainer.dispose()

      expect((OAuthProviderContainer as any).instance).to.be.null
    })

    it('should do nothing when instance is null', async () => {
      ;(OAuthProviderContainer as any).instance = null
      await OAuthProviderContainer.dispose()
      // Should not throw
    })
  })
})
