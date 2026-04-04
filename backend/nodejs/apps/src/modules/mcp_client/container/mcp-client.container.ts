import { Container } from 'inversify';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { McpClientService } from '../services/mcp-client.service';

const loggerConfig = {
  service: 'McpClient',
};

export class McpClientContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
  ): Promise<Container> {
    const container = new Container();
    const config: AppConfig = await loadAppConfig();

    // Bind configuration
    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => config)
      .inTransientScope();

    // Bind logger
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);

    // Initialize and bind services
    await this.initializeServices(container, config, configurationManagerConfig);

    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    config: AppConfig,
    configurationManagerConfig: ConfigurationManagerConfig,
  ): Promise<void> {
    try {
      // KeyValueStoreService
      const keyValueStoreService = KeyValueStoreService.getInstance(
        configurationManagerConfig,
      );
      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);

      // EncryptionService
      const encryptionService = EncryptionService.getInstance(
        configurationManagerConfig.algorithm,
        configurationManagerConfig.secretKey,
      );
      container
        .bind<EncryptionService>('EncryptionService')
        .toConstantValue(encryptionService);

      // AuthMiddleware
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

      // McpClientService
      const appBaseUrl = config.tokenBackend;
      const frontendBaseUrl = config.frontendUrl;
      const mcpClientService = new McpClientService(
        this.logger,
        keyValueStoreService,
        encryptionService,
        appBaseUrl,
        frontendBaseUrl,
      );
      container
        .bind<McpClientService>('McpClientService')
        .toConstantValue(mcpClientService);
    } catch (error) {
      const logger = container.get<Logger>('Logger');
      logger.error('Failed to initialize MCP client services', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('MCP client container not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      try {
        this.logger.info('MCP client services disconnected successfully');
      } catch (error) {
        this.logger.error('Error while disconnecting MCP client services', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      } finally {
        this.instance = null!;
      }
    }
  }
}
