import { Container } from 'inversify';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { IMessageProducer } from '../../../libs/types/messaging.types';
import {
  resolveMessageBrokerConfig,
  createMessageProducer,
} from '../../../libs/services/message-broker.factory';

const loggerConfig = { service: 'McpServers' };

/**
 * MCP Servers Container
 *
 * Dependency injection container for the MCP servers module.
 */
export class McpServersContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
  ): Promise<Container> {
    const container = new Container();
    const config: AppConfig = await loadAppConfig();

    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => config)
      .inTransientScope();

    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);

    await this.initializeServices(container, config);

    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    config: AppConfig,
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

      const brokerConfig = resolveMessageBrokerConfig(config);
      const messageProducer = createMessageProducer(
        brokerConfig,
        container.get('Logger'),
      );
      await messageProducer.connect();
      container
        .bind<IMessageProducer>('MessageProducer')
        .toConstantValue(messageProducer);

      const jwtSecret = config.jwtSecret;
      const scopedJwtSecret = config.scopedJwtSecret;
      if (!jwtSecret || !scopedJwtSecret) {
        throw new Error('JWT secrets are missing in configuration');
      }
      const authTokenService = new AuthTokenService(jwtSecret, scopedJwtSecret);
      const authMiddleware = new AuthMiddleware(
        container.get('Logger'),
        authTokenService,
      );
      container
        .bind<AuthMiddleware>('AuthMiddleware')
        .toConstantValue(authMiddleware);
    } catch (error) {
      const logger = container.get<Logger>('Logger');
      logger.error('Failed to initialize MCP servers services', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('MCP servers container not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      try {
        const messageProducer = this.instance.isBound('MessageProducer')
          ? this.instance.get<IMessageProducer>('MessageProducer')
          : null;

        if (messageProducer && messageProducer.isConnected()) {
          await messageProducer.disconnect();
        }
        this.logger.info('MCP servers services disconnected successfully');
      } catch (error) {
        this.logger.error('Error while disconnecting MCP servers services', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      } finally {
        this.instance = null!;
      }
    }
  }
}
