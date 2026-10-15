import { Container } from 'inversify';
import { NotificationService } from '../service/notification.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { TYPES } from '../../../libs/types/container.types';
import { NotificationProducer } from '../service/notification.producer';
import { NotificationConsumer } from '../service/notification.consumer';
import { Logger } from '../../../libs/services/logger.service';
import { IMessageConsumer } from '../../../libs/types/messaging.types';
import { createNotificationMessageConsumer } from '../../../libs/services/message-broker.factory';
import {
  INotificationEmailDispatcher,
  NOTIFICATION_EMAIL_DISPATCHER,
  noNotificationEmails,
} from '../service/notification-email.port';

const loggerConfig = {
  service: 'NotificationContainer',
};

export class NotificationContainer {
  private static container: Container | null = null;
  private static logger: Logger = Logger.getInstance(loggerConfig);
  /** Run on dispose, for services bound into the container after `initialize`. */
  private static disposers: Array<() => Promise<void>> = [];

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

    container.bind(NotificationService).toSelf().inSingletonScope();

    container.bind(NotificationProducer).toSelf().inSingletonScope();
    container.bind(NotificationConsumer).toSelf().inSingletonScope();

    container
      .bind<INotificationEmailDispatcher>(NOTIFICATION_EMAIL_DISPATCHER)
      .toConstantValue(noNotificationEmails);

    this.container = container;
    return container;
  }

  /** Registers a shutdown step; it runs once, on the next `dispose`. */
  static onDispose(disposer: () => Promise<void>): void {
    this.disposers.push(disposer);
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
    const disposers = this.disposers;
    this.disposers = [];
    for (const dispose of disposers) {
      try {
        await dispose();
      } catch {
        // ignore disconnect errors during shutdown
      }
    }
    c.unbindAll();
    this.container = null;
  }
}
