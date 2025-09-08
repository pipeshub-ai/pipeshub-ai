import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { MailService } from '../services/mail.service';
import { OrgController } from '../controller/org.controller';
import { UserController } from '../controller/users.controller';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { UserGroupController } from '../controller/userGroups.controller';
import { EntitiesEventProducer } from '../services/entity_events.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthService } from '../services/auth.service';
import { ConfigurationManagerService } from '../services/cm.service';
import { TeamsController } from '../controller/teams.controller';

const loggerConfig = {
  service: 'User Manager Container',
};
export class UserManagerContainer {
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
      const mailService = new MailService(appConfig, container.get('Logger'));
      container
        .bind<MailService>('MailService')
        .toDynamicValue(() => mailService);

      const authService = new AuthService(appConfig, container.get('Logger'));
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

      const entityEventsService = new EntitiesEventProducer(
        appConfig.kafka,
        container.get('Logger'),
      );
      container
        .bind<EntitiesEventProducer>('EntitiesEventProducer')
        .toConstantValue(entityEventsService);

      // Rebind controllers
      container.bind<OrgController>('OrgController').toDynamicValue(() => {
        return new OrgController(
          appConfig,
          container.get<MailService>('MailService'),
          container.get('Logger'),
          container.get<EntitiesEventProducer>('EntitiesEventProducer'),
        );
      });
      
      container.bind<TeamsController>('TeamsController').toDynamicValue(() => {
        return new TeamsController(appConfig, container.get('Logger'));
      });

      container.bind<UserController>('UserController').toDynamicValue(() => {
        return new UserController(
          appConfig,
          container.get<MailService>('MailService'),
          container.get<AuthService>('AuthService'),
          container.get('Logger'),
          container.get<EntitiesEventProducer>('EntitiesEventProducer'),
        );
      });

      const userGroupController = new UserGroupController();
      container
        .bind<UserGroupController>('UserGroupController')
        .toConstantValue(userGroupController);

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
        // Get specific services that need to be disconnected

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
        const entityEventsService = this.instance.isBound(
          'EntitiesEventProducer',
        )
          ? this.instance.get<EntitiesEventProducer>('EntitiesEventProducer')
          : null;
        // Disconnect services if they have a disconnect method
        if (entityEventsService && entityEventsService.isConnected()) {
          this.logger.info('Entity Events disconnected successfully');
          await entityEventsService.stop();
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
