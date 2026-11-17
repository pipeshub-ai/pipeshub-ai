import { Container } from 'inversify';
import { NotificationService } from '../service/notification.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { TYPES } from '../../../libs/types/container.types';
import { NotificationProducer } from '../service/notification.producer';
import { NotificationConsumer } from '../service/notification.consumer';
import { Logger } from '../../../libs/services/logger.service';
import {
  IMessageConsumer,
  IMessageProducer,
} from '../../../libs/types/messaging.types';
import {
  createMessageProducer,
  createNotificationMessageConsumer,
  resolveMessageBrokerConfig,
} from '../../../libs/services/message-broker.factory';
import { MailProducer } from '../../mail/services/mail.producer';
import {
  PLATFORM_ADMIN_RECIPIENTS,
  PlatformAdminRecipientsResolver,
  PlatformAlertHandler,
  logIfPlatformAlertsBlocked,
} from '../service/platform-alert.handler';
// Who receives installation-wide alerts is chosen in src/config.ts.
import { resolvePlatformAdminRecipients } from '../../../config';

const loggerConfig = {
  service: 'NotificationContainer',
};

export class NotificationContainer {
  private static container: Container | null = null;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(appConfig: AppConfig): Promise<Container> {
    const container = new Container();
    const authTokenService = new AuthTokenService(
      appConfig.jwtSecret,
      appConfig.scopedJwtSecret,
    );
    container
      .bind<AuthTokenService>(TYPES.AuthTokenService)
      .toConstantValue(authTokenService);
    container.bind<Logger>('Logger').toConstantValue(this.logger);

    const messageConsumer: IMessageConsumer = createNotificationMessageConsumer(
      appConfig,
      this.logger,
    );
    container
      .bind<IMessageConsumer>('MessageConsumer')
      .toConstantValue(messageConsumer);

    // Connected on first use: only installation-wide alerts that email publish here.
    const messageProducer: IMessageProducer = createMessageProducer(
      resolveMessageBrokerConfig(appConfig),
      this.logger,
    );
    container
      .bind<IMessageProducer>('MessageProducer')
      .toConstantValue(messageProducer);

    container.bind(NotificationService).toSelf().inSingletonScope();
    container.bind(MailProducer).toSelf().inSingletonScope();
    container
      .bind<PlatformAdminRecipientsResolver>(PLATFORM_ADMIN_RECIPIENTS)
      .toConstantValue(() => resolvePlatformAdminRecipients());
    container.bind(PlatformAlertHandler).toSelf().inSingletonScope();
    logIfPlatformAlertsBlocked(this.logger);

    container.bind(NotificationProducer).toSelf().inSingletonScope();
    container.bind(NotificationConsumer).toSelf().inSingletonScope();

    this.container = container;
    return container;
  }

  /** Cross-container access for session force-logout emits (e.g. role change). */
  static getNotificationService(): NotificationService | null {
    if (!this.container) {
      return null;
    }
    try {
      return this.container.get(NotificationService);
    } catch {
      return null;
    }
  }

  static async dispose(): Promise<void> {
    if (!this.container) {
      return;
    }
    const c = this.container;
    try {
      if (c.isBound('MessageConsumer')) {
        const consumer = c.get<IMessageConsumer>('MessageConsumer');
        if (consumer.isConnected()) {
          await consumer.disconnect();
        }
      }
    } catch {
      // ignore disconnect errors during shutdown
    }
    try {
      if (c.isBound('MessageProducer')) {
        const producer = c.get<IMessageProducer>('MessageProducer');
        if (producer.isConnected()) {
          await producer.disconnect();
        }
      }
    } catch {
      // ignore disconnect errors during shutdown
    }
    c.unbindAll();
    this.container = null;
  }
}
