import { Container } from 'inversify';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { DesktopProxySocketGateway } from '../socket/desktop-proxy.gateway';

export class DesktopProxyContainer {
  private static container: Container | null = null;

  static async initialize(
    appConfig: AppConfig,
    getPort: () => number,
  ): Promise<Container> {
    const container = new Container();

    const authTokenService = new AuthTokenService(
      appConfig.jwtSecret,
      appConfig.scopedJwtSecret,
    );

    container
      .bind<AuthTokenService>(AuthTokenService)
      .toConstantValue(authTokenService);

    container
      .bind<DesktopProxySocketGateway>(DesktopProxySocketGateway)
      .toDynamicValue((ctx) => {
        const auth = ctx.container.get<AuthTokenService>(AuthTokenService);
        return new DesktopProxySocketGateway(auth, getPort);
      })
      .inSingletonScope();

    this.container = container;
    return container;
  }

  static async dispose(): Promise<void> {
    if (this.container) {
      this.container.unbindAll();
      this.container = null;
    }
  }
}
