import { Container } from 'inversify';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { CliRpcSocketGateway } from '../socket/cli_rpc_socket_gateway';

export class CliRpcContainer {
  private static container: Container | null = null;

  /**
   * @param getPort returns the port the HTTP server is actually listening on,
   *   so the RPC gateway proxies REST calls to the right loopback port instead
   *   of re-reading process.env.PORT (which may not match the resolved port).
   */
  static async initialize(
    appConfig: AppConfig,
    getPort: () => number,
  ): Promise<Container> {
    const container = new Container();
    const authTokenService = new AuthTokenService(
      appConfig.jwtSecret,
      appConfig.scopedJwtSecret,
    );
    container.bind(AuthTokenService).toConstantValue(authTokenService);
    container
      .bind(CliRpcSocketGateway)
      .toDynamicValue((ctx) => {
        const auth = ctx.container.get(AuthTokenService);
        return new CliRpcSocketGateway(auth, getPort);
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
