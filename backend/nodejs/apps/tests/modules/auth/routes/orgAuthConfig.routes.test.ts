import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createOrgAuthConfigRouter } from '../../../../src/modules/auth/routes/orgAuthConfig.routes';
import { UserAccountController } from '../../../../src/modules/auth/controller/userAccount.controller';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service';

describe('createOrgAuthConfigRouter', () => {
  let container: Container;
  let mockUserAccountController: any;
  let mockConfig: any;
  let mockPrometheusService: any;

  beforeEach(() => {
    container = new Container();

    mockUserAccountController = {
      getAuthMethod: sinon.stub(),
      setUpAuthConfig: sinon.stub(),
      updateAuthMethod: sinon.stub(),
    };

    mockConfig = {
      jwtSecret: 'test-secret',
      scopedJwtSecret: 'test-scoped',
    };

    mockPrometheusService = {
      recordActivity: sinon.stub(),
    };

    container
      .bind<UserAccountController>('UserAccountController')
      .toConstantValue(mockUserAccountController);
    container
      .bind<AppConfig>('AppConfig')
      .toConstantValue(mockConfig as any);
    container
      .bind<PrometheusService>(PrometheusService)
      .toConstantValue(mockPrometheusService as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should return an Express router', () => {
    const router = createOrgAuthConfigRouter(container);
    expect(router).to.exist;
    expect(router).to.have.property('stack');
  });

  it('should register GET /authMethods route', () => {
    const router = createOrgAuthConfigRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const authMethodsRoute = routes.find(
      (r: any) => r.path === '/authMethods',
    );
    expect(authMethodsRoute).to.exist;
    expect(authMethodsRoute?.methods.get).to.be.true;
  });

  it('should register POST / route for setup', () => {
    const router = createOrgAuthConfigRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const setupRoute = routes.find((r: any) => r.path === '/');
    expect(setupRoute).to.exist;
    expect(setupRoute?.methods.post).to.be.true;
  });

  it('should register POST /updateAuthMethod route', () => {
    const router = createOrgAuthConfigRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const updateRoute = routes.find(
      (r: any) => r.path === '/updateAuthMethod',
    );
    expect(updateRoute).to.exist;
    expect(updateRoute?.methods.post).to.be.true;
  });

  it('should have attachContainerMiddleware as a non-route middleware', () => {
    const router = createOrgAuthConfigRouter(container);
    const middlewareLayers = router.stack.filter(
      (layer: any) => !layer.route,
    );
    expect(middlewareLayers.length).to.be.greaterThanOrEqual(1);
  });
});
