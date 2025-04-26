import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { MailController } from '../controller/mail.controller';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';

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
      container.bind<MailController>('MailController').toDynamicValue(() => {
        return new MailController(appConfig, container.get('Logger'));
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
      this.instance = null!;
      this.logger.info('Mail Services Successfully disconnected');
    }
  }
}
