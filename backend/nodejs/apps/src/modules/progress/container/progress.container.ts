import { Container } from 'inversify';
import { RedisService } from '../../../libs/services/redis.service';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { NotificationService } from '../../notification/service/notification.service';
import { ProgressService } from '../service/progress.service';

const loggerConfig = { service: 'ProgressContainer' };

export class ProgressContainer {
  private static container: Container | null = null;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  /**
   * @param notificationService the SAME instance that owns the Socket.IO server
   *   (created in NotificationContainer and initialized with the HTTP server in
   *   app.ts), so the ticker emits over the live socket.
   */
  static async initialize(
    appConfig: AppConfig,
    notificationService: NotificationService,
  ): Promise<Container> {
    const container = new Container();
    container.bind<Logger>('Logger').toConstantValue(this.logger);

    // Dedicated Redis connection for the progress ticker. Progress keys are read
    // via getClient() (raw, unprefixed) to match the Python writers.
    const redisService = new RedisService(appConfig.redis, this.logger);
    container.bind<RedisService>('RedisService').toConstantValue(redisService);

    container
      .bind<NotificationService>(NotificationService)
      .toConstantValue(notificationService);

    container.bind(ProgressService).toSelf().inSingletonScope();

    this.container = container;
    return container;
  }

  static async dispose(): Promise<void> {
    if (!this.container) return;
    try {
      this.container.get<ProgressService>(ProgressService).stop();
      await this.container.get<RedisService>('RedisService').disconnect();
    } catch {
      // ignore shutdown errors
    }
    this.container.unbindAll();
    this.container = null;
  }
}
