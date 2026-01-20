import { Container } from 'inversify'
import { Logger } from '../../../libs/services/logger.service'
import { AuthTokenService } from '../../../libs/services/authtoken.service'
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware'
import { EncryptionService } from '../../../libs/encryptor/encryptor'
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config'
import { AppConfig } from '../../tokens_manager/config/config'
import { ConfigService } from '../../tokens_manager/services/cm.service'
import { JwtConfig, getJwtConfig } from '../../../libs/utils/jwtConfig'
import { OAuthAppService } from '../services/oauth.app.service'
import { OAuthTokenService } from '../services/oauth_token.service'
import { AuthorizationCodeService } from '../services/authorization_code.service'
import { ScopeValidatorService } from '../services/scope.validator.service'
import { OAuthAppController } from '../controller/oauth.app.controller'
import { OAuthProviderController } from '../controller/oauth.provider.controller'
import { OIDCProviderController } from '../controller/oid.provider.controller'
import { OAuthAuthMiddleware } from '../middlewares/oauth.auth.middleware'

const loggerConfig = {
  service: 'OAuth Provider',
}

export class OAuthProviderContainer {
  private static instance: Container
  private static logger: Logger = Logger.getInstance(loggerConfig)

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container()

    // Bind configuration
    container.bind<Logger>('Logger').toConstantValue(this.logger)
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig)
    // Bind configuration manager
    container
      .bind<ConfigService>('ConfigService')
      .toConstantValue(ConfigService.getInstance())
    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => appConfig)
      .inTransientScope()

    // Bind secrets
    container.bind<string>('JWT_SECRET').toConstantValue(appConfig.jwtSecret)
    container
      .bind<string>('SCOPED_JWT_SECRET')
      .toConstantValue(appConfig.scopedJwtSecret)

    // OAuth issuer - use backend URL if not explicitly configured
    const oauthIssuer =
      appConfig.oauthIssuer ||
      `${appConfig.authBackend}/api/v1/oauth-provider`
    container.bind<string>('OAUTH_ISSUER').toConstantValue(oauthIssuer)

    await this.initializeServices(
      container,
      configurationManagerConfig,
      appConfig,
    )

    this.instance = container
    return container
  }

  private static async initializeServices(
    container: Container,
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      const logger = container.get<Logger>('Logger')

      // Initialize Encryption Service
      const encryptionService = EncryptionService.getInstance(
        configurationManagerConfig.algorithm,
        configurationManagerConfig.secretKey,
      )
      container
        .bind<EncryptionService>('EncryptionService')
        .toConstantValue(encryptionService)

      // Initialize Auth Services
      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      )
      container
        .bind<AuthTokenService>('AuthTokenService')
        .toConstantValue(authTokenService)

      const authMiddleware = new AuthMiddleware(logger, authTokenService)
      container
        .bind<AuthMiddleware>('AuthMiddleware')
        .toConstantValue(authMiddleware)

      // Initialize OAuth Services
      const scopeValidatorService = new ScopeValidatorService()
      container
        .bind<ScopeValidatorService>('ScopeValidatorService')
        .toConstantValue(scopeValidatorService)

      const authorizationCodeService = new AuthorizationCodeService(logger)
      container
        .bind<AuthorizationCodeService>('AuthorizationCodeService')
        .toConstantValue(authorizationCodeService)

      const oauthAppService = new OAuthAppService(
        logger,
        encryptionService,
        scopeValidatorService,
      )
      container
        .bind<OAuthAppService>('OAuthAppService')
        .toConstantValue(oauthAppService)

      // Get JWT configuration from platform config
      const jwtConfig = await getJwtConfig(logger)
      container.bind<JwtConfig>('JwtConfig').toConstantValue(jwtConfig)

      const oauthTokenService = new OAuthTokenService(
        logger,
        jwtConfig,
        container.get<string>('OAUTH_ISSUER'),
      )
      container
        .bind<OAuthTokenService>('OAuthTokenService')
        .toConstantValue(oauthTokenService)

      // Initialize OAuth Auth Middleware
      const oauthAuthMiddleware = new OAuthAuthMiddleware(
        logger,
        oauthTokenService,
        scopeValidatorService,
      )
      container
        .bind<OAuthAuthMiddleware>('OAuthAuthMiddleware')
        .toConstantValue(oauthAuthMiddleware)

      // Initialize Controllers
      container
        .bind<OAuthAppController>('OAuthAppController')
        .toDynamicValue(() => {
          return new OAuthAppController(
            logger,
            oauthAppService,
            oauthTokenService,
            scopeValidatorService,
          )
        })

      container
        .bind<OAuthProviderController>('OAuthProviderController')
        .toDynamicValue(() => {
          return new OAuthProviderController(
            logger,
            oauthAppService,
            oauthTokenService,
            authorizationCodeService,
            scopeValidatorService,
          )
        })

      container
        .bind<OIDCProviderController>('OIDCProviderController')
        .toDynamicValue(() => {
          return new OIDCProviderController(
            oauthTokenService,
            scopeValidatorService,
            appConfig,
          )
        })

      this.logger.info('OAuth Provider services initialized successfully')
    } catch (error) {
      this.logger.error('Failed to initialize OAuth Provider services', {
        error: error instanceof Error ? error.message : 'Unknown error',
      })
      throw error
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('OAuth Provider container not initialized')
    }
    return this.instance
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      try {
        this.logger.info('OAuth Provider services disposed successfully')
      } catch (error) {
        this.logger.error('Error while disposing OAuth Provider services', {
          error: error instanceof Error ? error.message : 'Unknown error',
        })
      } finally {
        this.instance = null!
      }
    }
  }
}
