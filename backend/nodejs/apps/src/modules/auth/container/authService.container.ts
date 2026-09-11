import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import {
  RedisService,
  getSharedRedisService,
} from '../../../libs/services/redis.service';
import { IRedisConnectionProvider } from '../../../libs/services/redis/connectionProvider.interface';
import { getRedisProvider } from '../../../libs/services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../../libs/services/redis/connectionConfig';
import { IamService } from '../services/iam.service';
import { MailService } from '../services/mail.service';
import { SessionService } from '../services/session.service';
import { SamlController } from '../controller/saml.controller';
import { UserAccountController } from '../controller/userAccount.controller';
import { ConfigurationManagerService } from '../services/cm.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { JitProvisioningService } from '../services/jit-provisioning.service';
import { EntitiesEventProducer } from '../../user_management/services/entity_events.service';
import { IMessageProducer } from '../../../libs/types/messaging.types';
import {
  resolveMessageBrokerConfig,
  createMessageProducer,
} from '../../../libs/services/message-broker.factory';

const loggerConfig = {
  service: 'Auth Service Container',
};

export class AuthServiceContainer {
  protected static instance: Container;
  protected static logger: Logger = Logger.getInstance(loggerConfig);

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
  protected static async initializeServices(
    container: Container,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      const logger = container.get<Logger>('Logger');
      // Shared across containers (R11): the auth and token-manager containers
      // both need cache access, and two instances means two connections --
      // two full cluster topologies on MemoryDB.
      const redisService = getSharedRedisService(
        appConfig.redis,
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
              host: appConfig.redis.host,
              port: appConfig.redis.port,
              username: appConfig.redis.username,
              password: appConfig.redis.password,
              db: appConfig.redis.db,
            }),
          ),
        );
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
      const authMiddleware = new AuthMiddleware(logger, authTokenService);
      container
        .bind<AuthMiddleware>('AuthMiddleware')
        .toConstantValue(authMiddleware);
      const iamService = new IamService(appConfig, logger);
      container.bind<IamService>('IamService').toConstantValue(iamService);
      const mailService = new MailService(appConfig, logger);
      container.bind<MailService>('MailService').toConstantValue(mailService);
      const sessionService = new SessionService(redisService);
      container
        .bind<SessionService>('SessionService')
        .toConstantValue(sessionService);

      const configurationService = new ConfigurationManagerService();
      container
        .bind<ConfigurationManagerService>('ConfigurationManagerService')
        .toConstantValue(configurationService);

      // Create broker-agnostic message producer
      const brokerConfig = resolveMessageBrokerConfig(appConfig);
      const messageProducer = createMessageProducer(brokerConfig, logger);
      await messageProducer.connect();

      container
        .bind<IMessageProducer>('MessageProducer')
        .toConstantValue(messageProducer);

      const entityEventsService = new EntitiesEventProducer(
        messageProducer,
        logger,
      );
      container
        .bind<EntitiesEventProducer>('EntitiesEventProducer')
        .toConstantValue(entityEventsService);

      // JIT Provisioning Service - shared service for user provisioning
      const jitProvisioningService = new JitProvisioningService(
        logger,
        entityEventsService,
      );
      container
        .bind<JitProvisioningService>('JitProvisioningService')
        .toConstantValue(jitProvisioningService);

      container.bind<SamlController>('SamlController').toDynamicValue(() => {
        return new SamlController(appConfig, logger);
      });

      container
        .bind<UserAccountController>('UserAccountController')
        .toDynamicValue(() => {
          return new UserAccountController(
            appConfig,
            iamService,
            mailService,
            sessionService,
            configurationService,
            logger,
            jitProvisioningService,
          );
        })
        .inSingletonScope();
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
        const redisService = this.instance.isBound('RedisService')
          ? this.instance.get<RedisService>('RedisService')
          : null;

        const keyValueStoreService = this.instance.isBound(
          'KeyValueStoreService',
        )
          ? this.instance.get<KeyValueStoreService>('KeyValueStoreService')
          : null;

        const messageProducer = this.instance.isBound('MessageProducer')
          ? this.instance.get<IMessageProducer>('MessageProducer')
          : null;

        if (redisService && redisService.isConnected()) {
          await redisService.disconnect();
        }

        if (keyValueStoreService && keyValueStoreService.isConnected()) {
          await keyValueStoreService.disconnect();
        }

        if (messageProducer && messageProducer.isConnected()) {
          await messageProducer.disconnect();
        }

        this.logger.info('All auth services disconnected successfully');
      } catch (error) {
        this.logger.error('Error while disconnecting auth services', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      } finally {
        this.instance = null!;
      }
    }
  }
}
