import { Container } from 'inversify';
import { AppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';

const loggerConfig = {
  service: 'Projects',
};

/**
 * Minimal DI container for the projects module. Project/ChatSession
 * persistence goes through the Mongoose models directly (see
 * `ProjectService`), the same pattern `enterprise_search` uses — Mongoose's
 * connection is a process-wide singleton set up by `MongoService`, so this
 * container only needs to provide what every authenticated route needs:
 * config, logging, and auth.
 *
 * @module projects/container
 */
export class ProjectsContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static initialize(
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
      .toDynamicValue(() => appConfig)
      .inTransientScope();

    const jwtSecret = appConfig.jwtSecret;
    const scopedJwtSecret = appConfig.scopedJwtSecret;
    if (!jwtSecret || !scopedJwtSecret) {
      throw new Error('JWT secrets are missing in configuration');
    }
    const authTokenService = new AuthTokenService(jwtSecret, scopedJwtSecret);
    const authMiddleware = new AuthMiddleware(
      container.get('Logger'),
      authTokenService,
    );
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue(authMiddleware);

    this.instance = container;
    return Promise.resolve(container);
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('Projects container not initialized');
    }
    return this.instance;
  }
}

export default ProjectsContainer;
