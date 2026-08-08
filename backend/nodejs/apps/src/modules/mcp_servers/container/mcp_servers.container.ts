import { Container } from 'inversify';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';

const loggerConfig = {
  service: 'McpServers',
};

/**
 * MCP Servers Container
 *
 * Minimal DI container for the MCP servers gateway module — it's a pure proxy
 * in front of the Python connectors service's `/api/v1/mcp-servers` router
 * (see `mcp_servers.controller.ts`), so like `SkillsContainer` it needs no
 * Kafka/message-producer or entity-event wiring, only what every
 * authenticated route needs: config, logging, and auth. Admin checks are
 * verified independently by the Python side (`_check_user_is_admin` calling
 * back into Node's `/adminCheck`), so this container has no admin-check
 * dependency either.
 *
 * @module mcp_servers/container
 */
export class McpServersContainer {
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
      throw new Error('MCP servers container not initialized');
    }
    return this.instance;
  }
}
