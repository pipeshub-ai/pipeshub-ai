import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { StorageController } from '../controllers/storage.controller';
import { SwaggerService } from '../../docs/swagger.container';
import { registerStorageSwagger } from '../docs/swagger';

const loggerConfig = {
  service: 'Storage service',
};

export class StorageContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container();
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);

    // Initialize and bind services
    await this.initializeServices(container, appConfig);

    // Register Swagger documentation if SwaggerService is available
    if (container.isBound(SwaggerService)) {
      const swaggerService = container.get<SwaggerService>(SwaggerService);
      registerStorageSwagger(swaggerService);
    }

    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      const keyValueStoreService = KeyValueStoreService.getInstance(
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig'),
      );

      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);
      this.logger.info('Storage services initialized successfully');

      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      );
      const authMiddleware = new AuthMiddleware(
        Logger.getInstance(loggerConfig),
        authTokenService,
      );
      container
        .bind<AuthMiddleware>('AuthMiddleware')
        .toConstantValue(authMiddleware);
      const storageConfig = appConfig.storage;
      container
        .bind<DefaultStorageConfig>('StorageConfig')
        .toDynamicValue(() => storageConfig) // Always fetch latest reference
        .inTransientScope();

      container
        .bind<StorageController>('StorageController')
        .toDynamicValue(() => {
          return new StorageController(
            storageConfig,
            Logger.getInstance(loggerConfig),
            keyValueStoreService,
          );
        });
    } catch (error) {
      this.logger.error('Failed to initialize storage services', {
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
    try {
      if (!this.instance) {
        this.logger?.info(
          'Storage container already disposed or not initialized',
        );
        return;
      }

      // Get only services that need to be disconnected
      const keyValueStoreService = this.instance.isBound('KeyValueStoreService')
        ? this.instance.get<KeyValueStoreService>('KeyValueStoreService')
        : null;

      // Disconnect with timeout
      if (
        keyValueStoreService &&
        typeof keyValueStoreService.disconnect === 'function'
      ) {
        try {
          const disconnectPromise = keyValueStoreService.disconnect();
          await Promise.race([
            disconnectPromise,
            new Promise((resolve) => setTimeout(resolve, 2000)), // 2-second timeout
          ]);
        } catch (error) {
          this.logger?.error('Error disconnecting KeyValueStoreService', {
            error: error instanceof Error ? error.message : 'Unknown error',
          });
        }
      }

      this.logger?.info('All Storage services disconnected successfully');
    } catch (error) {
      this.logger?.error('Error in dispose', { error });
    } finally {
      // Always clear the instance reference to prevent memory leaks
      this.instance = null!;
    }
  }
}
