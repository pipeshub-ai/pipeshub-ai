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
import { SamlController } from '../../auth/controller/saml.controller';
import { IMessageProducer } from '../../../libs/types/messaging.types';
import {
  resolveMessageBrokerConfig,
  createMessageProducer,
} from '../../../libs/services/message-broker.factory';

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

      // Create broker-agnostic message producer
      const brokerConfig = resolveMessageBrokerConfig(appConfig);
      const messageProducer = createMessageProducer(brokerConfig, container.get('Logger'));
      await messageProducer.connect();

      container
        .bind<IMessageProducer>('MessageProducer')
        .toConstantValue(messageProducer);

      const syncEventsService = new SyncEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      container
        .bind<SyncEventProducer>('SyncEventProducer')
        .toConstantValue(syncEventsService);

      const entityEventsService = new EntitiesEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      container
        .bind<EntitiesEventProducer>('EntitiesEventProducer')
        .toConstantValue(entityEventsService);

      container.bind<ConfigService>('ConfigService').toDynamicValue(() => {
        return new ConfigService(appConfig, container.get('Logger'));
      });
      container.bind<SamlController>('SamlController').toDynamicValue(() => {
        return new SamlController(appConfig, container.get('Logger'));
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
        const keyValueStoreService = this.instance.isBound(
          'KeyValueStoreService',
        )
          ? this.instance.get<KeyValueStoreService>('KeyValueStoreService')
          : null;

        const messageProducer = this.instance.isBound('MessageProducer')
          ? this.instance.get<IMessageProducer>('MessageProducer')
          : null;

        if (keyValueStoreService && keyValueStoreService.isConnected()) {
          await keyValueStoreService.disconnect();
          this.logger.info('KeyValueStoreService disconnected successfully');
        }

        if (messageProducer && messageProducer.isConnected()) {
          await messageProducer.disconnect();
          this.logger.info('MessageProducer disconnected successfully');
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
