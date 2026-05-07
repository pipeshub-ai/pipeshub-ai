/**
 * Inversify wiring for the internal REST proxy used by desktop clients
 * (e.g. Electron) to call the API over an authenticated socket namespace.
 */
import { Container } from 'inversify';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { RestProxySocketGateway } from '../socket/socket_gateway';

export class RestProxyContainer {
  private static container: Container | null = null;

  /**
   * @param getPort returns the port the HTTP server is actually listening on,
   *   so the gateway proxies REST calls to the right loopback port instead
   *   of re-reading process.env.PORT (which may not match the resolved port).
   */
  static initialize(appConfig: AppConfig, getPort: () => number): Container {
    const container = new Container();
    const authTokenService = new AuthTokenService(
      appConfig.jwtSecret,
      appConfig.scopedJwtSecret,
    );
    container.bind(AuthTokenService).toConstantValue(authTokenService);
    container
      .bind(RestProxySocketGateway)
      .toDynamicValue((ctx) => {
        const auth = ctx.container.get(AuthTokenService);
        return new RestProxySocketGateway(auth, getPort);
      })
      .inSingletonScope();
    this.container = container;
    return container;
  }

  static dispose(): void {
    if (this.container) {
      this.container.unbindAll();
    }
  }
}
