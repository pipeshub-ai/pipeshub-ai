import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { ConfigService } from '../services/updateConfig.service';
import { SamlController } from '../../auth/controller/saml.controller';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';

export enum CMContainerToken {
  ConfigurationManagerConfig = 'ConfigurationManagerConfig',
  AppConfig = 'AppConfig',
  Logger = 'Logger',
  KeyValueStoreService = 'KeyValueStoreService',
  ConfigService = 'ConfigService',
  AuthMiddleware = 'AuthMiddleware',
  SamlController = 'SamlController',
}

const loggerConfig = {
  service: 'Configuration Manager Service',
};

export class ConfigurationManagerContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container();

    // Bind configuration
    container
      .bind<ConfigurationManagerConfig>(CMContainerToken.ConfigurationManagerConfig)
      .toConstantValue(configurationManagerConfig);
    container.bind<AppConfig>(CMContainerToken.AppConfig).toConstantValue(appConfig);
    // Bind logger
    container.bind<Logger>(CMContainerToken.Logger).toConstantValue(this.logger);

    // Initialize and bind services
    await this.initializeServices(container, appConfig);

    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      const configurationManagerConfig =
        container.get<ConfigurationManagerConfig>(
          CMContainerToken.ConfigurationManagerConfig,
        );
      const keyValueStoreService = KeyValueStoreService.getInstance(
        configurationManagerConfig,
      );

      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>(CMContainerToken.KeyValueStoreService)
        .toConstantValue(keyValueStoreService);

      container.bind<ConfigService>(CMContainerToken.ConfigService).toDynamicValue(() => {
        return new ConfigService(appConfig, container.get(CMContainerToken.Logger));
      });
      container.bind<SamlController>(CMContainerToken.SamlController).toDynamicValue(() => {
        return new SamlController(appConfig, container.get('Logger'));
      });

      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      );
      const authMiddleware = new AuthMiddleware(
        container.get(CMContainerToken.Logger),
        authTokenService,
      );
      container
        .bind<AuthMiddleware>(CMContainerToken.AuthMiddleware)
        .toConstantValue(authMiddleware);

      this.logger.debug("SCopedToken: " + authTokenService.generateScopedToken({
        scopes:   Object.values(TokenScopes),
      }));
      this.logger.info(
        'Configuration Manager services initialized successfully',
      );
    } catch (error) {
      this.logger.error('Failed to initialize Configuration Manager services', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('Service container not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      try {
        // Get only services that need to be disconnected
        const keyValueStoreService = this.instance.isBound(
          CMContainerToken.KeyValueStoreService,
        )
          ? this.instance.get<KeyValueStoreService>(
              CMContainerToken.KeyValueStoreService,
            )
          : null;

        // Disconnect services if they have a disconnect method
        if (keyValueStoreService && keyValueStoreService.isConnected()) {
          await keyValueStoreService.disconnect();
          this.logger.info('KeyValueStoreService disconnected successfully');
        }

        this.logger.info(
          'All configuration manager services disconnected successfully',
        );
      } catch (error) {
        this.logger.error(
          'Error while disconnecting configuration manager services',
          {
            error: error instanceof Error ? error.message : 'Unknown error',
          },
        );
      } finally {
        this.instance = null!;
      }
    }
  }
}
