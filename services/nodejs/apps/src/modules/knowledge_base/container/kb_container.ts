import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { ArangoService } from '../../../libs/services/arango.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { RecordsEventProducer } from '../services/records_events.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { SyncEventProducer } from '../services/sync_events.service';
const loggerConfig = {
  service: 'Knowledge Base Service',
};

export class KnowledgeBaseContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container();
    this.logger.info(' In the init  kb conatiner');
    // Bind configuration
    // Bind logger
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);
    container.bind<AppConfig>('AppConfig').toConstantValue(appConfig);

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
      const arangoService = new ArangoService(appConfig.arango);
      await arangoService.initialize();
      container
        .bind<ArangoService>('ArangoService')
        .toConstantValue(arangoService);

      const configurationManagerConfig =
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig');
      const keyValueStoreService = KeyValueStoreService.getInstance(
        configurationManagerConfig,
      );
      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);

      this.logger.info('before events producer');

      const recordsEventProducer = new RecordsEventProducer(
        appConfig.kafka,
        this.logger,
      );

      // Start the Kafka producer
      await recordsEventProducer.start();

      container
        .bind<RecordsEventProducer>('RecordsEventProducer')
        .toConstantValue(recordsEventProducer);

      this.logger.info('After events producer binding');

      const syncEventProducer = new SyncEventProducer(
        appConfig.kafka,
        this.logger,
      );

      // start the kafka producer for sync-events
      await syncEventProducer.start();

      container
        .bind<SyncEventProducer>('SyncEventProducer')
        .toConstantValue(syncEventProducer);
        
      this.logger.info('After sync producer binding');

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
      this.logger.info('Knowledge Base services initialized successfully');
    } catch (error) {
      this.logger.error('Failed to initialize Knowledge Base services', {
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
        // Stop the Kafka producer
        if (this.instance.isBound('RecordsEventProducer')) {
          const recordsEventProducer = this.instance.get<RecordsEventProducer>(
            'RecordsEventProducer',
          );
          await recordsEventProducer.stop();
        }

        // stop the sync-event kafka
        if (this.instance.isBound('SyncEventProducer')) {
          const syncEventProducer =
            this.instance.get<SyncEventProducer>('SyncEventProducer');
          await syncEventProducer.stop();
        }
        const keyValueStoreService = this.instance.isBound(
          'KeyValueStoreService',
        )
          ? this.instance.get<KeyValueStoreService>('KeyValueStoreService')
          : null;

        // Disconnect services if they have a disconnect method
        if (keyValueStoreService && keyValueStoreService.isConnected()) {
          await keyValueStoreService.disconnect();
          this.logger.info('KeyValueStoreService disconnected successfully');
        }

        this.instance = null!;
        this.logger.info(
          'All services in Knowledge base successfully disposed',
        );
      } catch (error) {
        this.logger.error('Error during service disposal', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }
  }
}
