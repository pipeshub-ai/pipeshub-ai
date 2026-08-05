import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { MailController } from '../controller/mail.controller';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  IMessageConsumer,
  IMessageProducer,
} from '../../../libs/types/messaging.types';
import {
  createMailMessageConsumer,
  createMessageProducer,
  resolveMessageBrokerConfig,
} from '../../../libs/services/message-broker.factory';
import { MailSenderService } from '../services/mail.sender.service';
import { MailConsumer } from '../services/mail.consumer';
import { MailProducer } from '../services/mail.producer';
import { NotificationProducer } from '../../notification/service/notification.producer';

const loggerConfig = {
  service: 'Mail Service',
};
export class MailServiceContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);
  static async initialize(appConfig: AppConfig): Promise<Container> {
    const container = new Container();
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => appConfig) // Always fetch latest reference
      .inTransientScope();
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
      container
        .bind<() => AppConfig>('AppConfigProvider')
        .toConstantValue(() => container.get<AppConfig>('AppConfig'));

      container.bind(MailSenderService).toSelf().inSingletonScope();

      try {
        // Own consumer group, so mail offsets track independently of notifications.
        const messageProducer = createMessageProducer(
          resolveMessageBrokerConfig(appConfig),
          container.get('Logger'),
        );
        await messageProducer.connect();
        container
          .bind<IMessageProducer>('MessageProducer')
          .toConstantValue(messageProducer);

        const messageConsumer: IMessageConsumer = createMailMessageConsumer(
          appConfig,
          container.get('Logger'),
        );
        container
          .bind<IMessageConsumer>('MessageConsumer')
          .toConstantValue(messageConsumer);

        container.bind(MailProducer).toSelf().inSingletonScope();
        container.bind(NotificationProducer).toSelf().inSingletonScope();
        container.bind(MailConsumer).toSelf().inSingletonScope();
      } catch (brokerError) {
        container.get<Logger>('Logger').warn(
          'Mail broker unavailable; async delivery is disabled until restart',
          {
            error:
              brokerError instanceof Error
                ? brokerError.message
                : String(brokerError),
          },
        );
      }

      container.bind<MailController>('MailController').toDynamicValue(() => {
        return new MailController(
          appConfig,
          container.get('Logger'),
          container.get<MailSenderService>(MailSenderService),
        );
      });
      const jwtSecret = appConfig.jwtSecret;
      const scopedJwtSecret = appConfig.scopedJwtSecret;
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
      throw new Error('Mail Service container not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      const c = this.instance;
      try {
        if (c.isBound('MessageConsumer')) {
          const consumer = c.get<IMessageConsumer>('MessageConsumer');
          if (consumer.isConnected()) {
            await consumer.disconnect();
          }
        }
      } catch (error) {
        this.logger.warn('Mail consumer failed to disconnect during shutdown', {
          error: error instanceof Error ? error.message : String(error),
        });
      }
      try {
        if (c.isBound('MessageProducer')) {
          const producer = c.get<IMessageProducer>('MessageProducer');
          if (producer.isConnected()) {
            await producer.disconnect();
          }
        }
      } catch (error) {
        this.logger.warn('Mail producer failed to disconnect during shutdown', {
          error: error instanceof Error ? error.message : String(error),
        });
      }
      this.instance = null!;
      this.logger.info('Mail Services Successfully disconnected');
    }
  }
}
