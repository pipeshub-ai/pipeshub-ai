import { Container } from 'inversify';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';

/**
 * API Toolsets Container
 *
 * The module is a pure proxy in front of the Python connectors service's
 * `/api/v1/api-toolsets` router, so it needs only config, logging and auth.
 * Admin checks happen on the Python side, against the live caller role.
 *
 * @module api_toolsets/container
 */
export class ApiToolsetsContainer {
  private static logger: Logger = Logger.getInstance({
    service: 'ApiToolsets',
  });

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

    const { jwtSecret, scopedJwtSecret } = config;
    if (jwtSecret === '' || scopedJwtSecret === '') {
      throw new Error('JWT secrets are missing in configuration');
    }
    const authTokenService = new AuthTokenService(jwtSecret, scopedJwtSecret);
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue(
        new AuthMiddleware(container.get('Logger'), authTokenService),
      );

    return container;
  }
}
