import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';

const loggerConfig = {
  service: 'Enterprise Search Service',
};

export class EnterpriseSearchAgentContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container();

    // Bind configuration
    // Bind logger
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);
    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => appConfig) // Always fetch latest reference
      .inTransientScope();
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
      // Initialize services

      const keyValueStoreService = KeyValueStoreService.getInstance(
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig'),
      );

      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);
      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      );
      const authMiddleware = new AuthMiddleware(
        container.get('Logger'),
        authTokenService,
      );
      container
        .bind<AuthMiddleware>('AuthMiddleware')
        .toConstantValue(authMiddleware);
      this.logger.info(
        'Enterprise Search Agent services initialized successfully',
      );
    } catch (error) {
      this.logger.error(
        'Failed to initialize Enterprise Search Agent services',
        {
          error: error instanceof Error ? error.message : 'Unknown error',
        },
      );
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
    try {
      // Get only services that need to be disconnected
      const keyValueStoreService = this.instance.isBound('KeyValueStoreService')
        ? this.instance.get<KeyValueStoreService>('KeyValueStoreService')
        : null;

      // Disconnect services if they have a disconnect method
      if (keyValueStoreService && keyValueStoreService.isConnected()) {
        await keyValueStoreService.disconnect();
        this.logger.info('KeyValueStoreService disconnected successfully');
      }

      this.logger.info(
        'All Enterprise Search services disconnected successfully',
      );
    } catch (error) {
      this.logger.error(
        'Error while disconnecting Enterprise Search services',
        {
          error: error instanceof Error ? error.message : 'Unknown error',
        },
      );
    } finally {
      this.instance = null!;
    }
  }
}
