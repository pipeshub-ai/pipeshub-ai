import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { RedisService } from '../../../libs/services/redis.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { CrawlingWorkerService } from '../services/crawling_worker';
import { CrawlingSchedulerService } from '../services/crawling_service';
import { RedisConfig } from '../../../libs/types/redis.types';
import { ConnectorsCrawlingService } from '../services/connectors/connectors';
import { SyncEventProducer } from '../../knowledge_base/services/sync_events.service';

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
      setupCrawlingDependencies(container, appConfig.redis);

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

      const syncEventsService = new SyncEventProducer(
        appConfig.kafka,
        container.get('Logger'),
      );
      await syncEventsService.start();
      container
        .bind<SyncEventProducer>('SyncEventProducer')
        .toConstantValue(syncEventsService);

      logger.info('Starting crawling worker service...');
      const crawlingWorkerService = container.get<CrawlingWorkerService>(
        CrawlingWorkerService,
      );

      if (!crawlingWorkerService) {
        throw new Error('CrawlingWorkerService not found');
      }

      logger.info('Crawling worker service started successfully');

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

        const syncEventsService = this.instance.isBound('SyncEventProducer')
          ? this.instance.get<SyncEventProducer>('SyncEventProducer')
          : null;
          
        if (redisService && redisService.isConnected()) {
          await redisService.disconnect();
        }

        const crawlingWorkerService = this.instance.isBound(
          CrawlingWorkerService,
        )
          ? this.instance.get<CrawlingWorkerService>(CrawlingWorkerService)
          : null;
        if (crawlingWorkerService) {
          await crawlingWorkerService.close();
        }

        const keyValueStoreService = this.instance.isBound(KeyValueStoreService)
          ? this.instance.get<KeyValueStoreService>(KeyValueStoreService)
          : null;
        if (keyValueStoreService && keyValueStoreService.isConnected()) {
          await keyValueStoreService.disconnect();
          this.logger.info('KeyValueStoreService disconnected successfully');
        }

        if (syncEventsService && syncEventsService.isConnected()) {
          await syncEventsService.disconnect();
          this.logger.info('SyncEventProducer disconnected successfully');
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

export function setupCrawlingDependencies(
  container: Container,
  redisConfig: RedisConfig,
): void {
  // Bind Redis config
  container.bind<RedisConfig>('RedisConfig').toConstantValue(redisConfig);

  // Bind crawling connector services

  container
    .bind<ConnectorsCrawlingService>(ConnectorsCrawlingService)
    .to(ConnectorsCrawlingService)
    .inSingletonScope();

  // Bind task factory
  // Removed CrawlingTaskFactory; inject ConnectorsCrawlingService directly

  // Bind core services
  container
    .bind<CrawlingSchedulerService>(CrawlingSchedulerService)
    .to(CrawlingSchedulerService)
    .inSingletonScope();

  // Bind crawling worker service
  container
    .bind<CrawlingWorkerService>(CrawlingWorkerService)
    .to(CrawlingWorkerService)
    .inSingletonScope();
}
