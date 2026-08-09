import { Container } from 'inversify';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';

const loggerConfig = {
  service: 'Workflows',
};

/**
 * Workflows Container
 *
 * Minimal DI container for the workflows gateway module -- it's a pure proxy
 * in front of the Python query service's `/api/v1/workflows` router (see
 * `workflows.controller.ts`), same shape as `TasksContainer`: no Kafka/
 * message-producer wiring needed, only what every authenticated route needs
 * (config, logging, auth).
 *
 * @module workflows/container
 */
export class WorkflowsContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
  ): Promise<Container> {
    const container = new Container();
    const config: AppConfig = await loadAppConfig();

    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => config)
      .inTransientScope();
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);

    const jwtSecret = config.jwtSecret;
    const scopedJwtSecret = config.scopedJwtSecret;
    if (!jwtSecret || !scopedJwtSecret) {
      throw new Error('JWT secrets are missing in configuration');
    }
    const authTokenService = new AuthTokenService(jwtSecret, scopedJwtSecret);
    const authMiddleware = new AuthMiddleware(container.get('Logger'), authTokenService);
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(authMiddleware);

    this.instance = container;
    return container;
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('Workflows container not initialized');
    }
    return this.instance;
  }
}
