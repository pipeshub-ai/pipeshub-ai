import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware, DELEGATION_AVAILABLE } from '../../../config';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AppConfig } from '../../tokens_manager/config/config';
import { ConfigService } from '../../tokens_manager/services/cm.service';
import { JwtConfig, getJwtConfig } from '../../../libs/utils/jwtConfig';
import { OAuthAppService } from '../services/oauth.app.service';
import { OAuthTokenService } from '../services/oauth_token.service';
import { AuthorizationCodeService } from '../services/authorization_code.service';
import { ScopeValidatorService } from '../services/scope.validator.service';
import { PatService } from '../services/pat.service';
import { ServiceTokenService } from '../services/service-token.service';
import { DelegationService } from '../services/delegation.service';
import { TokenExchangeService } from '../services/token-exchange.service';
import { OAuthDcrService } from '../services/oauth.dcr.service';
import { OAuthDeviceService } from '../services/oauth.device.service';
import { FirstPartyDeviceAppService } from '../services/oauth.first_party_device.service';
import { OAuthAppController } from '../controller/oauth.app.controller';
import { OAuthProviderController } from '../controller/oauth.provider.controller';
import { OIDCProviderController } from '../controller/oid.provider.controller';
import { PatController } from '../controller/pat.controller';
import { ServiceTokenController } from '../controller/service-token.controller';
import { OAuthAuthMiddleware } from '../middlewares/oauth.auth.middleware';

const loggerConfig = {
  service: 'OAuth Provider',
};

export class OAuthProviderContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container();

    // Bind configuration
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);
    // Bind configuration manager
    container
      .bind<ConfigService>('ConfigService')
      .toConstantValue(ConfigService.getInstance());
    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => appConfig)
      .inTransientScope();

    // Bind secrets
    container.bind<string>('JWT_SECRET').toConstantValue(appConfig.jwtSecret);
    container
      .bind<string>('SCOPED_JWT_SECRET')
      .toConstantValue(appConfig.scopedJwtSecret);

    // OAuth issuer - use backend URL if not explicitly configured
    const oauthIssuer =
      appConfig.oauthIssuer || `${appConfig.authBackend}/api/v1/oauth-provider`;
    container.bind<string>('OAUTH_ISSUER').toConstantValue(oauthIssuer);

    await this.initializeServices(
      container,
      configurationManagerConfig,
      appConfig,
    );

    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      const logger = container.get<Logger>('Logger');

      // Initialize Encryption Service
      const encryptionService = EncryptionService.getInstance(
        configurationManagerConfig.algorithm,
        configurationManagerConfig.secretKey,
      );
      container
        .bind<EncryptionService>('EncryptionService')
        .toConstantValue(encryptionService);

      // Initialize Auth Services
      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      );
      container
        .bind<AuthTokenService>('AuthTokenService')
        .toConstantValue(authTokenService);

      const authMiddleware = new AuthMiddleware(logger, authTokenService);
      container
        .bind<AuthMiddleware>('AuthMiddleware')
        .toConstantValue(authMiddleware);

      // Initialize OAuth Services
      const scopeValidatorService = new ScopeValidatorService();
      container
        .bind<ScopeValidatorService>('ScopeValidatorService')
        .toConstantValue(scopeValidatorService);

      const authorizationCodeService = new AuthorizationCodeService(logger);
      container
        .bind<AuthorizationCodeService>('AuthorizationCodeService')
        .toConstantValue(authorizationCodeService);

      // Get JWT configuration from platform config
      const jwtConfig = await getJwtConfig(logger);
      container.bind<JwtConfig>('JwtConfig').toConstantValue(jwtConfig);

      // Built before the app service, which needs it to revoke an app's
      // outstanding tokens when its identity changes.
      const oauthTokenService = new OAuthTokenService(
        logger,
        jwtConfig,
        container.get<string>('OAUTH_ISSUER'),
      );
      container
        .bind<OAuthTokenService>('OAuthTokenService')
        .toConstantValue(oauthTokenService);

      const oauthAppService = new OAuthAppService(
        logger,
        encryptionService,
        scopeValidatorService,
        oauthTokenService,
      );
      container
        .bind<OAuthAppService>('OAuthAppService')
        .toConstantValue(oauthAppService);

      // Initialize OAuth Auth Middleware
      const oauthAuthMiddleware = new OAuthAuthMiddleware(
        logger,
        oauthTokenService,
        scopeValidatorService,
      );
      container
        .bind<OAuthAuthMiddleware>('OAuthAuthMiddleware')
        .toConstantValue(oauthAuthMiddleware);

      const configService = container.get<ConfigService>('ConfigService');
      const patService = new PatService(
        logger,
        encryptionService,
        configService,
        oauthTokenService,
        scopeValidatorService,
      );
      container.bind<PatService>('PatService').toConstantValue(patService);

      // Delegation: the exchange that turns a service token into a
      // short-lived token for one named person, and the allow-list it checks
      // before it will do so.
      //
      // The service is bound in both editions because the exchange and the
      // auth middleware read from it on every delegated request. What is not
      // bound here is the controller behind the administrator routes, which
      // is the only thing that can write an allow-list entry: it lives in the
      // Enterprise overlay, and without it the exchange has nothing to find.
      const delegationService = new DelegationService(logger);
      container
        .bind<DelegationService>('DelegationService')
        .toConstantValue(delegationService);

      const serviceTokenService = new ServiceTokenService(
        logger,
        encryptionService,
        configService,
        oauthTokenService,
        scopeValidatorService,
        delegationService,
      );
      container
        .bind<ServiceTokenService>('ServiceTokenService')
        .toConstantValue(serviceTokenService);
      container
        .bind<ServiceTokenController>('ServiceTokenController')
        .toConstantValue(
          new ServiceTokenController(
            serviceTokenService,
            scopeValidatorService,
          ),
        );

      // One place decides whether this edition offers delegation; everything
      // that needs to know reads it from here.
      container
        .bind<boolean>('DelegationAvailable')
        .toConstantValue(DELEGATION_AVAILABLE);

      const tokenExchangeService = new TokenExchangeService(
        logger,
        oauthTokenService,
        delegationService,
        DELEGATION_AVAILABLE,
      );
      container
        .bind<TokenExchangeService>('TokenExchangeService')
        .toConstantValue(tokenExchangeService);

      const oauthDcrService = new OAuthDcrService(
        logger,
        oauthAppService,
        scopeValidatorService,
        appConfig,
      );
      container
        .bind<OAuthDcrService>('OAuthDcrService')
        .toConstantValue(oauthDcrService);

      const firstPartyDeviceAppService = new FirstPartyDeviceAppService(
        logger,
        encryptionService,
        scopeValidatorService,
        appConfig,
      );
      container
        .bind<FirstPartyDeviceAppService>('FirstPartyDeviceAppService')
        .toConstantValue(firstPartyDeviceAppService);

      const oauthDeviceService = new OAuthDeviceService(
        logger,
        oauthAppService,
        oauthTokenService,
        scopeValidatorService,
        firstPartyDeviceAppService,
      );
      container
        .bind<OAuthDeviceService>('OAuthDeviceService')
        .toConstantValue(oauthDeviceService);

      // Initialize Controllers
      container
        .bind<OAuthAppController>('OAuthAppController')
        .toDynamicValue(() => {
          return new OAuthAppController(
            logger,
            oauthAppService,
            oauthTokenService,
            scopeValidatorService,
          );
        });

      container.bind<PatController>('PatController').toDynamicValue(() => {
        return new PatController(logger, patService, scopeValidatorService);
      });

      container
        .bind<OAuthProviderController>('OAuthProviderController')
        .toDynamicValue(() => {
          return new OAuthProviderController(
            logger,
            oauthAppService,
            oauthTokenService,
            authorizationCodeService,
            scopeValidatorService,
            oauthDcrService,
            oauthDeviceService,
            tokenExchangeService,
          );
        });

      container
        .bind<OIDCProviderController>('OIDCProviderController')
        .toDynamicValue(() => {
          return new OIDCProviderController(
            oauthTokenService,
            scopeValidatorService,
            appConfig,
            firstPartyDeviceAppService,
            DELEGATION_AVAILABLE,
          );
        });

      this.logger.info('OAuth Provider services initialized successfully');
    } catch (error) {
      this.logger.error('Failed to initialize OAuth Provider services', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('OAuth Provider container not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      try {
        this.logger.info('OAuth Provider services disposed successfully');
      } catch (error) {
        this.logger.error('Error while disposing OAuth Provider services', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      } finally {
        this.instance = null!;
      }
    }
  }
}
