import { Container } from 'inversify';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { CliRpcSocketGateway } from '../socket/cli_rpc_socket_gateway';

export class CliRpcContainer {
  private static container: Container | null = null;

  static async initialize(appConfig: AppConfig): Promise<Container> {
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
        return new CliRpcSocketGateway(auth, () =>
          Number(process.env.PORT ?? '3000'),
        );
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
