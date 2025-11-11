import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { EntitiesEventProducer } from '../../user_management/services/entity_events.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { ConfigService } from '../services/updateConfig.service';
import { SyncEventProducer } from '../services/kafka_events.service';
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
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);
    container.bind<AppConfig>('AppConfig').toConstantValue(appConfig);
    // Bind logger
    container.bind<Logger>('Logger').toConstantValue(this.logger);

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
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig');
      const keyValueStoreService = KeyValueStoreService.getInstance(
        configurationManagerConfig,
      );

      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);

      const syncEventsService = new SyncEventProducer(
        appConfig.kafka,
        container.get('Logger'),
      );
      container
        .bind<SyncEventProducer>('SyncEventProducer')
        .toConstantValue(syncEventsService);

      const entityEventsService = new EntitiesEventProducer(
        appConfig.kafka,
        container.get('Logger'),
      );
      container
        .bind<EntitiesEventProducer>('EntitiesEventProducer')
        .toConstantValue(entityEventsService);

      container.bind<ConfigService>('ConfigService').toDynamicValue(() => {
        return new ConfigService(appConfig, container.get('Logger'));
      });

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
          'KeyValueStoreService',
        )
          ? this.instance.get<KeyValueStoreService>('KeyValueStoreService')
          : null;

        const entityEventsService = this.instance.isBound(
          'EntitiesEventProducer',
        )
          ? this.instance.get<EntitiesEventProducer>('EntitiesEventProducer')
          : null;

        const syncEventsService = this.instance.isBound('SyncEventProducer')
          ? this.instance.get<SyncEventProducer>('SyncEventProducer')
          : null;

        // Disconnect services if they have a disconnect method
        if (keyValueStoreService && keyValueStoreService.isConnected()) {
          await keyValueStoreService.disconnect();
          this.logger.info('KeyValueStoreService disconnected successfully');
        }

        if (entityEventsService && entityEventsService.isConnected()) {
          await entityEventsService.disconnect();
          this.logger.info('EntitiesEventProducer disconnected successfully');
        }

        if (syncEventsService && syncEventsService.isConnected()) {
          await syncEventsService.disconnect();
          this.logger.info('SyncEventProducer disconnected successfully');
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
