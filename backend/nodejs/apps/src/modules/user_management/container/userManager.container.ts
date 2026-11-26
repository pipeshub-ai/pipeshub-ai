import { createInboxStore } from '../../../libs/repositories/agent-harness/inbox';
import {
  INBOX_ERASER,
  type InboxEraser,
} from '../../agent-harness/ports/inbox';
import {
  isAgentHarnessInfrastructureReady,
  orgErasureJobsAvailable,
} from '../../agent-harness/config';
import {
  PRINCIPAL_DELETION,
  PrincipalDeletionService,
} from '../services/principal-deletion.service';
import { getRedisProvider } from '../../../libs/services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../../libs/services/redis/connectionConfig';
import { createEngineSignIns } from '../../../libs/repositories/agent-harness/engine-signin';
import { RedisEngineSignInRelay } from '../../agent-harness/adapters/redis/redis-engine-signin-relay';
import { engineSignInEraser } from '../../agent-harness/application/engine-signin-service';
import {
  ENGINE_SIGNIN_ERASER,
  type EngineSignInEraser,
} from '../../agent-harness/ports/engine-signins';
import { ConnectorPersonalInstanceEraser } from '../../agent-harness/adapters/connector-personal-instances';
import {
  PERSONAL_INSTANCE_ERASER,
  type PersonalInstanceEraser,
} from '../../agent-harness/ports/personal-instances';
import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { MailService } from '../services/mail.service';
import { OrgController } from '../controller/org.controller';
import { UserController } from '../controller/users.controller';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { createModelAccountVault } from '../../agent-harness/adapters/configuration/model-account-vault';
import { createScheduleStore } from '../../../libs/repositories/agent-harness/schedules';
import {
  SCHEDULE_ERASER,
  ScheduleEraser,
} from '../../agent-harness/ports/schedules';
import { createMemoryStore } from '../../../libs/repositories/agent-harness/memory';
import {
  DATA_ERASER_EXTENSION,
  DataEraserExtension,
} from '../../agent-harness/ports/data-eraser-extension';
import {
  MEMORY_ERASER,
  MemoryEraser,
} from '../../agent-harness/ports/memory-store';
import {
  MODEL_ACCOUNT_ERASER,
  ModelAccountEraser,
} from '../../agent-harness/ports/model-accounts';
import { UserGroupController } from '../controller/userGroups.controller';
import { EntitiesEventProducer } from '../services/entity_events.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthService } from '../services/auth.service';
import { ConfigurationManagerService } from '../services/cm.service';
import { TeamsController } from '../controller/teams.controller';
import { IMessageProducer } from '../../../libs/types/messaging.types';
import * as messageBrokerFactory from '../../../libs/services/message-broker.factory';
import { NotificationProducer } from '../../notification/service/notification.producer';
import { ServiceAccountsService } from '../services/service-accounts.service';
import { ServiceAccountsController } from '../controller/service-accounts.controller';
import { MailProducer } from '../../mail/services/mail.producer';
import {
  ORG_CONTROLLER_FACTORY,
  OrgControllerFactory,
  USER_CONTROLLER_FACTORY,
  UserControllerFactory,
} from './controller-factories';
import { HarnessArtifactEraser } from '../../enterprise_search/adapters/harness-artifact-eraser';
import type { OrgQueryServiceEraser } from '../../agent-harness/ports/principal-erasure';
import { ORG_QUERY_SERVICE_ERASER } from '../../agent-harness/binders/tokens';
import {
  PRINCIPAL_DEACTIVATION,
  type PrincipalDeactivationPort,
} from '../../agent-harness/application/principal-deactivation';

const loggerConfig = {
  service: 'User Manager Container',
};
export class UserManagerContainer {
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

      const authService = new AuthService(appConfig);
      container
        .bind<AuthService>('AuthService')
        .toDynamicValue(() => authService);

      const configurationService = new ConfigurationManagerService();
      container
        .bind<ConfigurationManagerService>('ConfigurationManagerService')
        .toConstantValue(configurationService);

      const keyValueStoreService = KeyValueStoreService.getInstance(
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig'),
      );

      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);

      // Create broker-agnostic message producer
      const brokerConfig = messageBrokerFactory.resolveMessageBrokerConfig(appConfig);
      const messageProducer = messageBrokerFactory.createMessageProducer(
        brokerConfig,
        container.get('Logger'),
      );
      await messageProducer.connect();

      container
        .bind<IMessageProducer>('MessageProducer')
        .toConstantValue(messageProducer);

      // MailService publishes to the mail topic instead of sending inline.
      const mailProducer = new MailProducer(messageProducer, container.get('Logger'));
      container.bind<MailProducer>(MailProducer).toConstantValue(mailProducer);
      const mailService = new MailService(
        appConfig,
        container.get('Logger'),
        mailProducer,
      );
      container
        .bind<MailService>('MailService')
        .toDynamicValue(() => mailService);

      const entityEventsService = new EntitiesEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      container
        .bind<EntitiesEventProducer>('EntitiesEventProducer')
        .toConstantValue(entityEventsService);

      const notificationProducer = new NotificationProducer(
        messageProducer,
        container.get('Logger'),
      );
      container
        .bind<NotificationProducer>('NotificationProducer')
        .toConstantValue(notificationProducer);

      // User and org deletion erase their model accounts (P2.4b).
      const modelAccounts = createModelAccountVault(keyValueStoreService);
      container
        .bind<ModelAccountEraser>(MODEL_ACCOUNT_ERASER)
        .toConstantValue(modelAccounts);
      // ...and disable their schedules (P8a): nothing runs as a deleted user.
      const schedules = createScheduleStore();
      container
        .bind<ScheduleEraser>(SCHEDULE_ERASER)
        .toConstantValue(schedules);
      // And their governed memories (P7.3): deleteMany on a missing collection is a no-op.
      const memories = createMemoryStore();
      container.bind<MemoryEraser>(MEMORY_ERASER).toConstantValue(memories);
      // Stores this build does not know of, if a binding before this container's adds one; none here.
      const extension = container.isBound(DATA_ERASER_EXTENSION)
        ? container.get<DataEraserExtension>(DATA_ERASER_EXTENSION)
        : null;
      // And their own Claude sign-ins (SIGNIN): records removed, live sandboxes told to sign out.
      const engineSignIns = engineSignInEraser(
        createEngineSignIns(),
        isAgentHarnessInfrastructureReady()
          ? new RedisEngineSignInRelay(
              getRedisProvider(
                redisConnectionConfigFromHostPort({
                  host: appConfig.redis.host,
                  port: appConfig.redis.port,
                  username: appConfig.redis.username,
                  password: appConfig.redis.password,
                  db: appConfig.redis.db,
                }),
              ),
            )
          : undefined,
      );
      container
        .bind<EngineSignInEraser>(ENGINE_SIGNIN_ERASER)
        .toConstantValue(engineSignIns);
      // G2: their Inbox settings, run-notice claims and watch leases.
      container
        .bind<InboxEraser>(INBOX_ERASER)
        .toConstantValue(createInboxStore());
      // And their own toolset and MCP instances, through the connectors service (main #4324).
      const personalInstances = new ConnectorPersonalInstanceEraser(() => {
        const config = container.get<AppConfig>('AppConfig');
        return {
          connectorBackend: config.connectorBackend,
          jwtSecret: config.jwtSecret,
          scopedJwtSecret: config.scopedJwtSecret,
        };
      });
      container
        .bind<PersonalInstanceEraser>(PERSONAL_INSTANCE_ERASER)
        .toConstantValue(personalInstances);
      // CLEANUP-G2: user deletion with a hand-over. It needs the harness's transactions; without them a
      // user's deletion erases at once, as before.
      const principalDeletion = isAgentHarnessInfrastructureReady()
        ? new PrincipalDeletionService()
        : null;
      if (principalDeletion !== null) {
        container
          .bind<PrincipalDeletionService>(PRINCIPAL_DELETION)
          .toConstantValue(principalDeletion);
      }
      // An org's delete hands the rest of its data to an erasure job wherever a delete erases, harness on or not.
      const orgPrincipalDeletion =
        principalDeletion ??
        (orgErasureJobsAvailable() ? new PrincipalDeletionService() : null);

      // An org's deletion erases what the query service holds for it, before the org is marked deleted.
      const orgQueryService = new HarnessArtifactEraser(() => {
        const config = container.get<AppConfig>('AppConfig');
        return {
          aiBackend: config.aiBackend,
          jwtSecret: config.jwtSecret,
          scopedJwtSecret: config.scopedJwtSecret,
        };
      });
      container
        .bind<OrgQueryServiceEraser>(ORG_QUERY_SERVICE_ERASER)
        .toConstantValue(orgQueryService);

      const orgControllerFor: OrgControllerFactory = (config) =>
        new OrgController(
          config,
          container.get<MailService>('MailService'),
          container.get('Logger'),
          container.get<EntitiesEventProducer>('EntitiesEventProducer'),
          modelAccounts,
          schedules,
          memories,
          engineSignIns,
          personalInstances,
          extension,
          container.get<InboxEraser>(INBOX_ERASER),
          orgQueryService,
          orgPrincipalDeletion,
        );
      container
        .bind<OrgControllerFactory>(ORG_CONTROLLER_FACTORY)
        .toConstantValue(orgControllerFor);
      container
        .bind<OrgController>('OrgController')
        .toDynamicValue(() => orgControllerFor(appConfig));
      
      container.bind<TeamsController>('TeamsController').toDynamicValue(() => {
        return new TeamsController(appConfig, container.get('Logger'));
      });

      const userControllerFor: UserControllerFactory = (config) =>
        new UserController(
          config,
          container.get<MailService>('MailService'),
          container.get<AuthService>('AuthService'),
          container.get('Logger'),
          container.get<EntitiesEventProducer>('EntitiesEventProducer'),
          container.get<NotificationProducer>('NotificationProducer'),
          modelAccounts,
          schedules,
          memories,
          engineSignIns,
          principalDeletion,
          personalInstances,
          extension,
          // Every eraser in the constructor's order, as the org controller's: one left out here was never run in
          // production, whatever the controller listed (UI-SWITCH, after main-sync's org fix). The deactivation port
          // is shared into this container after it is composed, so it is read when a controller is made.
          container.isBound(PRINCIPAL_DEACTIVATION)
            ? container.get<PrincipalDeactivationPort>(PRINCIPAL_DEACTIVATION)
            : null,
          null,
          container.get<InboxEraser>(INBOX_ERASER),
        );
      container
        .bind<UserControllerFactory>(USER_CONTROLLER_FACTORY)
        .toConstantValue(userControllerFor);
      container
        .bind<UserController>('UserController')
        .toDynamicValue(() => userControllerFor(appConfig));

      const serviceAccountsService = new ServiceAccountsService(
        container.get<EntitiesEventProducer>('EntitiesEventProducer'),
        modelAccounts,
        memories,
        principalDeletion,
        personalInstances,
        extension,
      );
      container
        .bind<ServiceAccountsService>('ServiceAccountsService')
        .toConstantValue(serviceAccountsService);

      container
        .bind<ServiceAccountsController>('ServiceAccountsController')
        .toConstantValue(new ServiceAccountsController(serviceAccountsService));

      const userGroupController = new UserGroupController();
      container
        .bind<UserGroupController>('UserGroupController')
        .toConstantValue(userGroupController);

      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      );
      container
        .bind<AuthTokenService>('AuthTokenService')
        .toConstantValue(authTokenService);
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

        this.logger.info('All User Manager services disconnected successfully');
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
