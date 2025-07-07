import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { RedisService } from '../../../libs/services/redis.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { CrawlingSchedulerService } from '../services/crawling_service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { RedisConfig } from '../../../libs/types/redis.types';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';

const loggerConfig = {
  service: 'Crawling Manager Container',
};

export class CrawlingManagerContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container();
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);

    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => appConfig) // Always fetch latest reference
      .inTransientScope();
    await this.initializeServices(container, appConfig);
    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      const logger = container.get<Logger>('Logger');
      logger.info('Initializing Crawling Manager services');

      const redisService = new RedisService(
        appConfig.redis,
        container.get('Logger'),
      );
      container
        .bind<RedisService>('RedisService')
        .toConstantValue(redisService);

      // Bind RedisConfig for CrawlingSchedulerService
      container
        .bind<RedisConfig>('RedisConfig')
        .toConstantValue(appConfig.redis);

      // Crawling Scheduler Service
      container
        .bind<CrawlingSchedulerService>(CrawlingSchedulerService)
        .to(CrawlingSchedulerService)
        .inSingletonScope();

      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      );
      const authMiddleware = new AuthMiddleware(
        container.get('Logger'),
        authTokenService,
      );
      container
        .bind<AuthMiddleware>(AuthMiddleware)
        .toConstantValue(authMiddleware);

      const configurationManagerConfig =
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig');
      const keyValueStoreService = KeyValueStoreService.getInstance(
        configurationManagerConfig,
      );

      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);

      this.logger.info('Crawling Manager services initialized successfully');
    } catch (error) {
      const logger = container.get<Logger>('Logger');
      logger.error('Failed to initialize services', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('Crawling Manager container not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      try {
        // Get specific services that need to be disconnected
        const redisService = this.instance.isBound('RedisService')
          ? this.instance.get<RedisService>('RedisService')
          : null;

        // Disconnect services if they have a disconnect method
        if (redisService && redisService.isConnected()) {
          await redisService.disconnect();
        }

        this.logger.info(
          'All crawling manager services disconnected successfully',
        );
      } catch (error) {
        this.logger.error(
          'Error while disconnecting crawling manager services',
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
