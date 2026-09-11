import { Container } from 'inversify';
import { AppConfig, loadAppConfig } from '../config/config';
import { MongoService } from '../../../libs/services/mongo.service';
import {
  RedisService,
  getSharedRedisService,
} from '../../../libs/services/redis.service';
import { IRedisConnectionProvider } from '../../../libs/services/redis/connectionProvider.interface';
import { getRedisProvider } from '../../../libs/services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../../libs/services/redis/connectionConfig';
import { Logger } from '../../../libs/services/logger.service';
import { TokenEventProducer } from '../services/token-event.producer';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { EntitiesEventProducer } from '../services/entity_event.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { IMessageProducer } from '../../../libs/types/messaging.types';
import { RecordsEventProducer } from '../../knowledge_base/services/records_events.service';
import { SyncEventProducer } from '../../knowledge_base/services/sync_events.service';
import {
  resolveMessageBrokerConfig,
  createMessageProducer,
} from '../../../libs/services/message-broker.factory';

const loggerConfig = {
  service: 'Token Manager',
};

export class TokenManagerContainer {
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
      .toDynamicValue(() => config) // Always fetch latest reference
      .inTransientScope();

    // Bind logger
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);
    // Initialize and bind services
    await this.initializeServices(container, config);

    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    config: AppConfig,
  ): Promise<void> {
    try {
      const mongoService = new MongoService(config.mongo);
      await mongoService.initialize();
      container
        .bind<MongoService>('MongoService')
        .toConstantValue(mongoService);

      // Shared across containers (R11): the auth and token-manager containers
      // both need cache access, and two instances means two connections --
      // two full cluster topologies on MemoryDB.
      const redisService = getSharedRedisService(
        config.redis,
        container.get('Logger'),
      );
      container
        .bind<RedisService>('RedisService')
        .toConstantValue(redisService);

      // Same fingerprint `RedisService` just resolved internally (Phase 5,
      // R11), so this returns the already-cached singleton -- not a second
      // connection. Bound so any service in this container can depend on
      // `IRedisConnectionProvider` directly instead of importing the
      // factory module itself.
      container
        .bind<IRedisConnectionProvider>('RedisConnectionProvider')
        .toConstantValue(
          getRedisProvider(
            redisConnectionConfigFromHostPort({
              host: config.redis.host,
              port: config.redis.port,
              username: config.redis.username,
              password: config.redis.password,
              db: config.redis.db,
            }),
          ),
        );

      // Initialize KeyValueStoreService for PrometheusService dependency
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
      const brokerConfig = resolveMessageBrokerConfig(config);
      const messageProducer = createMessageProducer(
        brokerConfig,
        container.get('Logger'),
      );
      await messageProducer.connect();

      container
        .bind<IMessageProducer>('MessageProducer')
        .toConstantValue(messageProducer);

      const tokenEventProducer = new TokenEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      await tokenEventProducer.start();
      container
        .bind<TokenEventProducer>('KafkaService')
        .toConstantValue(tokenEventProducer);

      const entityEventsService = new EntitiesEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      container
        .bind<EntitiesEventProducer>('EntitiesEventProducer')
        .toConstantValue(entityEventsService);

      const recordsEventProducer = new RecordsEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      await recordsEventProducer.start();
      container
        .bind<RecordsEventProducer>('RecordsEventProducer')
        .toConstantValue(recordsEventProducer);

      const syncEventProducer = new SyncEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      await syncEventProducer.start();
      container
        .bind<SyncEventProducer>('SyncEventProducer')
        .toConstantValue(syncEventProducer);

      const jwtSecret = config.jwtSecret;
      const scopedJwtSecret = config.scopedJwtSecret;
      // Substituting a placeholder here authenticated the gateway with a secret
      // that is published in this repository: any token signed with it verifies.
      // A missing secret must stop startup, not silently weaken auth.
      if (!jwtSecret || !scopedJwtSecret) {
        throw new Error(
          'JWT secrets are not configured; refusing to start the token manager',
        );
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
      logger.error('Failed to initialize services', {
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
        const mongoService = this.instance.isBound('MongoService')
          ? this.instance.get<MongoService>('MongoService')
          : null;

        const redisService = this.instance.isBound('RedisService')
          ? this.instance.get<RedisService>('RedisService')
          : null;

        const messageProducer = this.instance.isBound('MessageProducer')
          ? this.instance.get<IMessageProducer>('MessageProducer')
          : null;

        const recordsEventProducer = this.instance.isBound(
          'RecordsEventProducer',
        )
          ? this.instance.get<RecordsEventProducer>('RecordsEventProducer')
          : null;

        const syncEventProducer = this.instance.isBound('SyncEventProducer')
          ? this.instance.get<SyncEventProducer>('SyncEventProducer')
          : null;

        if (recordsEventProducer) {
          await recordsEventProducer.stop();
        }
        if (syncEventProducer) {
          await syncEventProducer.stop();
        }

        if (redisService && redisService.isConnected()) {
          await redisService.disconnect();
        }
        if (messageProducer && messageProducer.isConnected()) {
          await messageProducer.disconnect();
        }
        if (mongoService && mongoService.isConnected()) {
          await mongoService.destroy();
        }

        this.logger.info('All services disconnected successfully');
      } catch (error) {
        this.logger.error('Error while disconnecting services', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      } finally {
        this.instance = null!;
      }
    }
  }
}
