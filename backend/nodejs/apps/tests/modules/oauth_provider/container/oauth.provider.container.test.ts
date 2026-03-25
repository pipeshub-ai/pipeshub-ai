import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OAuthProviderContainer } from '../../../../src/modules/oauth_provider/container/oauth.provider.container'
import { ConfigService } from '../../../../src/modules/tokens_manager/services/cm.service'

describe('OAuthProviderContainer', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', () => {
    expect(OAuthProviderContainer).to.be.a('function')
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(OAuthProviderContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(OAuthProviderContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(OAuthProviderContainer.dispose).to.be.a('function')
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      const originalInstance = (OAuthProviderContainer as any).instance
      ;(OAuthProviderContainer as any).instance = null

      try {
        expect(() => OAuthProviderContainer.getInstance()).to.throw(
          'OAuth Provider container not initialized',
        )
      } finally {
        ;(OAuthProviderContainer as any).instance = originalInstance
      }
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (OAuthProviderContainer as any).instance
      ;(OAuthProviderContainer as any).instance = mockContainer

      try {
        const result = OAuthProviderContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(OAuthProviderContainer as any).instance = originalInstance
      }
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      const originalInstance = (OAuthProviderContainer as any).instance
      ;(OAuthProviderContainer as any).instance = null

      try {
        await OAuthProviderContainer.dispose()
        // Should not throw
      } finally {
        ;(OAuthProviderContainer as any).instance = originalInstance
      }
    })

    it('should set instance to null after dispose', async () => {
      const mockContainer = {}
      const originalInstance = (OAuthProviderContainer as any).instance
      ;(OAuthProviderContainer as any).instance = mockContainer

      try {
        await OAuthProviderContainer.dispose()
        expect((OAuthProviderContainer as any).instance).to.be.null
      } finally {
        if ((OAuthProviderContainer as any).instance !== null) {
          ;(OAuthProviderContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle errors during dispose gracefully', async () => {
      const originalInstance = (OAuthProviderContainer as any).instance
      // Set instance to an object that causes an error during logging
      ;(OAuthProviderContainer as any).instance = {}

      try {
        await OAuthProviderContainer.dispose()
        expect((OAuthProviderContainer as any).instance).to.be.null
      } finally {
        if ((OAuthProviderContainer as any).instance !== null) {
          ;(OAuthProviderContainer as any).instance = originalInstance
        }
      }
    })
  })

  describe('initialize', () => {
    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(OAuthProviderContainer.initialize.length).to.equal(2)
    })

    it('should create a container and bind all services when called with valid config', async () => {
      // We need to mock getJwtConfig to avoid platform config dependency
      const jwtConfigModule = require('../../../../src/libs/utils/jwtConfig')
      sinon.stub(jwtConfigModule, 'getJwtConfig').resolves({
        algorithm: 'RS256',
        publicKey: 'public-key',
        privateKey: 'private-key',
        accessTokenExpiresIn: 3600,
        refreshTokenExpiresIn: 86400,
      })

      // Stub ConfigService.getInstance to avoid SECRET_KEY env dependency
      sinon.stub(ConfigService, 'getInstance').returns({} as any)

      const configManagerConfig = {
        algorithm: 'aes-256-cbc',
        secretKey: '0123456789abcdef0123456789abcdef',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-secret',
        oauthIssuer: 'https://auth.test.com',
        authBackend: 'http://localhost:3001',
      }

      try {
        const container = await OAuthProviderContainer.initialize(
          configManagerConfig as any,
          appConfig as any,
        )

        expect(container).to.exist
        expect(container.isBound('Logger')).to.be.true
        expect(container.isBound('EncryptionService')).to.be.true
        expect(container.isBound('AuthTokenService')).to.be.true
        expect(container.isBound('AuthMiddleware')).to.be.true
        expect(container.isBound('ScopeValidatorService')).to.be.true
        expect(container.isBound('AuthorizationCodeService')).to.be.true
        expect(container.isBound('OAuthAppService')).to.be.true
        expect(container.isBound('OAuthTokenService')).to.be.true
        expect(container.isBound('OAuthAuthMiddleware')).to.be.true
        expect(container.isBound('OAuthAppController')).to.be.true
        expect(container.isBound('OAuthProviderController')).to.be.true
        expect(container.isBound('OIDCProviderController')).to.be.true

        // Verify getInstance returns the same container
        const instance = OAuthProviderContainer.getInstance()
        expect(instance).to.equal(container)
      } finally {
        await OAuthProviderContainer.dispose()
      }
    })

    it('should use backend URL as issuer when oauthIssuer is not configured', async () => {
      const jwtConfigModule = require('../../../../src/libs/utils/jwtConfig')
      sinon.stub(jwtConfigModule, 'getJwtConfig').resolves({
        algorithm: 'RS256',
        publicKey: 'public-key',
        privateKey: 'private-key',
        accessTokenExpiresIn: 3600,
        refreshTokenExpiresIn: 86400,
      })

      // Stub ConfigService.getInstance to avoid SECRET_KEY env dependency
      sinon.stub(ConfigService, 'getInstance').returns({} as any)

      const configManagerConfig = {
        algorithm: 'aes-256-cbc',
        secretKey: '0123456789abcdef0123456789abcdef',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-secret',
        authBackend: 'http://localhost:3001',
      }

      try {
        const container = await OAuthProviderContainer.initialize(
          configManagerConfig as any,
          appConfig as any,
        )
        expect(container).to.exist
        expect(container.get<string>('OAUTH_ISSUER')).to.equal('http://localhost:3001/api/v1/oauth-provider')
      } finally {
        await OAuthProviderContainer.dispose()
      }
    })

    it('should throw when services initialization fails', async () => {
      const jwtConfigModule = require('../../../../src/libs/utils/jwtConfig')
      sinon.stub(jwtConfigModule, 'getJwtConfig').rejects(new Error('JWT config failed'))

      // Stub ConfigService.getInstance to avoid SECRET_KEY env dependency
      sinon.stub(ConfigService, 'getInstance').returns({} as any)

      const configManagerConfig = {
        algorithm: 'aes-256-cbc',
        secretKey: '0123456789abcdef0123456789abcdef',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-secret',
        authBackend: 'http://localhost:3001',
      }

      try {
        await OAuthProviderContainer.initialize(
          configManagerConfig as any,
          appConfig as any,
        )
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('JWT config failed')
      }
    })
  })
})
