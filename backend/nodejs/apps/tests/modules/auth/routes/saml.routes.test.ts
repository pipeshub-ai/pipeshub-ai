import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createSamlRouter } from '../../../../src/modules/auth/routes/saml.routes';
import { IamService } from '../../../../src/modules/auth/services/iam.service';
import { SessionService } from '../../../../src/modules/auth/services/session.service';
import { SamlController } from '../../../../src/modules/auth/controller/saml.controller';
import { MailService } from '../../../../src/modules/auth/services/mail.service';
import { ConfigurationManagerService } from '../../../../src/modules/auth/services/cm.service';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';
import { Logger } from '../../../../src/libs/services/logger.service';
import { UserAccountController } from '../../../../src/modules/auth/controller/userAccount.controller';
import { JitProvisioningService } from '../../../../src/modules/auth/services/jit-provisioning.service';

describe('createSamlRouter', () => {
  let container: Container;
  let mockConfig: any;
  let mockLogger: any;

  beforeEach(() => {
    container = new Container();

    mockConfig = {
      jwtSecret: 'test-secret',
      scopedJwtSecret: 'test-scoped',
      cookieSecret: 'test-cookie-secret',
      frontendUrl: 'http://frontend:3000',
      authBackend: 'http://auth:3000',
      cmBackend: 'http://cm:3001',
      iamBackend: 'http://iam:3000',
    };

    mockLogger = {
      info: sinon.stub(),
      debug: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    };

    const mockIamService = {
      getUserByEmail: sinon.stub(),
      getUserById: sinon.stub(),
      updateUser: sinon.stub(),
      checkAdminUser: sinon.stub(),
      createOrg: sinon.stub(),
      createUser: sinon.stub(),
    };

    const mockSessionService = {
      createSession: sinon.stub(),
      getSession: sinon.stub(),
      updateSession: sinon.stub(),
      completeAuthentication: sinon.stub(),
      deleteSession: sinon.stub(),
    };

    const mockSamlController = {
      signInViaSAML: sinon.stub(),
      getSamlEmailKeyByOrgId: sinon.stub(),
      updateSAMLStrategy: sinon.stub(),
    };

    const mockMailService = {
      sendMail: sinon.stub(),
    };

    const mockConfigService = {
      getConfig: sinon.stub(),
    };

    const mockAuthMiddleware = {
      scopedTokenValidator: sinon.stub().returns(
        (_req: any, _res: any, next: any) => next(),
      ),
    };

    const mockJitService = {
      provisionUser: sinon.stub(),
      extractGoogleUserDetails: sinon.stub(),
      extractMicrosoftUserDetails: sinon.stub(),
      extractOAuthUserDetails: sinon.stub(),
      extractSamlUserDetails: sinon.stub(),
    };

    container.bind<AppConfig>('AppConfig').toConstantValue(mockConfig as any);
    container.bind<Logger>('Logger').toConstantValue(mockLogger as any);
    container.bind<IamService>('IamService').toConstantValue(mockIamService as any);
    container.bind<SessionService>('SessionService').toConstantValue(mockSessionService as any);
    container.bind<SamlController>('SamlController').toConstantValue(mockSamlController as any);
    container.bind<MailService>('MailService').toConstantValue(mockMailService as any);
    container.bind<ConfigurationManagerService>('ConfigurationManagerService').toConstantValue(mockConfigService as any);
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any);
    container.bind<UserAccountController>('UserAccountController').toConstantValue({} as any);
    container.bind<JitProvisioningService>('JitProvisioningService').toConstantValue(mockJitService as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should return an Express router', () => {
    const router = createSamlRouter(container);
    expect(router).to.exist;
    expect(router).to.have.property('stack');
  });

  it('should register GET /signIn route', () => {
    const router = createSamlRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const signInRoute = routes.find((r: any) => r.path === '/signIn');
    expect(signInRoute).to.exist;
    expect(signInRoute?.methods.get).to.be.true;
  });

  it('should register POST /signIn/callback route', () => {
    const router = createSamlRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const callbackRoute = routes.find(
      (r: any) => r.path === '/signIn/callback',
    );
    expect(callbackRoute).to.exist;
    expect(callbackRoute?.methods.post).to.be.true;
  });

  it('should register POST /updateAppConfig route', () => {
    const router = createSamlRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const updateConfigRoute = routes.find(
      (r: any) => r.path === '/updateAppConfig',
    );
    expect(updateConfigRoute).to.exist;
    expect(updateConfigRoute?.methods.post).to.be.true;
  });

  it('should use passport middleware', () => {
    const router = createSamlRouter(container);
    // The router should have middleware layers (non-route layers) for passport
    const middlewareLayers = router.stack.filter(
      (layer: any) => !layer.route,
    );
    // Should have at least: attachContainer, session, passport.initialize, passport.session
    expect(middlewareLayers.length).to.be.greaterThanOrEqual(3);
  });

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createSamlRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      // GET /signIn, POST /signIn/callback, POST /updateAppConfig = 3
      expect(routes.length).to.equal(3);
    });
  });

  describe('middleware chains', () => {
    it('should have middleware layers on signIn route', () => {
      const router = createSamlRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const signInRoute = routes.find(
        (layer: any) => layer.route.path === '/signIn' && layer.route.methods.get,
      );
      expect(signInRoute).to.not.be.undefined;
      // The handler itself
      expect(signInRoute.route.stack.length).to.be.greaterThanOrEqual(1);
    });

    it('should have passport auth middleware on callback route', () => {
      const router = createSamlRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const callbackRoute = routes.find(
        (layer: any) => layer.route.path === '/signIn/callback' && layer.route.methods.post,
      );
      expect(callbackRoute).to.not.be.undefined;
      // passport.authenticate + handler
      expect(callbackRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });

    it('should have scoped token validator on updateAppConfig route', () => {
      const router = createSamlRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const updateConfigRoute = routes.find(
        (layer: any) => layer.route.path === '/updateAppConfig' && layer.route.methods.post,
      );
      expect(updateConfigRoute).to.not.be.undefined;
      // scopedTokenValidator + handler
      expect(updateConfigRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });

    it('should use session, passport.initialize, and passport.session as router-level middleware', () => {
      const router = createSamlRouter(container);
      const middlewareLayers = router.stack.filter(
        (layer: any) => !layer.route,
      );
      // attachContainer + session + passport.initialize + passport.session = at least 4
      expect(middlewareLayers.length).to.be.greaterThanOrEqual(4);
    });
  });

  describe('router configuration', () => {
    it('should create different router instances on each call', () => {
      const router1 = createSamlRouter(container);
      const router2 = createSamlRouter(container);

      expect(router1).to.not.equal(router2);
    });

    it('should have consistent route count across calls', () => {
      const router1 = createSamlRouter(container);
      const router2 = createSamlRouter(container);

      const routes1 = router1.stack.filter((layer: any) => layer.route);
      const routes2 = router2.stack.filter((layer: any) => layer.route);

      expect(routes1.length).to.equal(routes2.length);
    });
  });

  describe('route methods', () => {
    it('GET /signIn should only accept GET', () => {
      const router = createSamlRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      const signInRoute = routes.find(
        (layer: any) => layer.route.path === '/signIn',
      );
      expect(signInRoute).to.not.be.undefined;
      expect(signInRoute.route.methods.get).to.be.true;
      expect(signInRoute.route.methods.post).to.be.undefined;
    });

    it('POST /signIn/callback should only accept POST', () => {
      const router = createSamlRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      const callbackRoute = routes.find(
        (layer: any) => layer.route.path === '/signIn/callback',
      );
      expect(callbackRoute).to.not.be.undefined;
      expect(callbackRoute.route.methods.post).to.be.true;
      expect(callbackRoute.route.methods.get).to.be.undefined;
    });

    it('POST /updateAppConfig should only accept POST', () => {
      const router = createSamlRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      const updateRoute = routes.find(
        (layer: any) => layer.route.path === '/updateAppConfig',
      );
      expect(updateRoute).to.not.be.undefined;
      expect(updateRoute.route.methods.post).to.be.true;
      expect(updateRoute.route.methods.get).to.be.undefined;
    });
  });

  describe('route handler counts', () => {
    it('signIn route should have at least 1 handler', () => {
      const router = createSamlRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      const signInRoute = routes.find(
        (layer: any) => layer.route.path === '/signIn' && layer.route.methods.get,
      );
      expect(signInRoute).to.not.be.undefined;
      // Just the async handler wrapper
      expect(signInRoute.route.stack.length).to.be.greaterThanOrEqual(1);
    });

    it('callback route should have passport middleware + handler', () => {
      const router = createSamlRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      const callbackRoute = routes.find(
        (layer: any) => layer.route.path === '/signIn/callback' && layer.route.methods.post,
      );
      expect(callbackRoute).to.not.be.undefined;
      // passport.authenticate + handler
      expect(callbackRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });
  });

  describe('middleware layer types', () => {
    it('should include session middleware as non-route layer', () => {
      const router = createSamlRouter(container);
      const middlewareLayers = (router as any).stack.filter(
        (layer: any) => !layer.route,
      );
      // Should have at least attachContainer + session + passport.init + passport.session
      expect(middlewareLayers.length).to.be.greaterThanOrEqual(4);
    });

    it('no route should have zero handlers', () => {
      const router = createSamlRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      for (const routeLayer of routes) {
        expect(
          routeLayer.route.stack.length,
          `Route ${routeLayer.route.path} should have at least 1 handler`,
        ).to.be.greaterThanOrEqual(1);
      }
    });
  });

  describe('route paths', () => {
    it('should not have duplicate route paths with same method', () => {
      const router = createSamlRouter(container);
      const routes = (router as any).stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => {
          const methods = Object.keys(layer.route.methods).filter(
            (m: string) => layer.route.methods[m],
          );
          return `${methods.join(',')}:${layer.route.path}`;
        });

      const uniqueRoutes = new Set(routes);
      expect(uniqueRoutes.size).to.equal(routes.length);
    });
  });

  describe('route handler invocations', () => {
    function findRouteHandler(router: any, path: string, method: string) {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === path && l.route.methods[method],
      );
      if (!layer) return undefined;
      const handlers = layer.route.stack.map((s: any) => s.handle);
      return handlers[handlers.length - 1];
    }

    function createMockReqRes() {
      const mockReq: any = {
        user: { email: 'test@example.com' },
        tokenPayload: { userId: 'user123', orgId: 'org123' },
        body: { RelayState: '' },
        params: {},
        query: {},
        headers: {},
        sessionInfo: null,
        ip: '127.0.0.1',
      };
      const mockRes: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
        cookie: sinon.stub(),
        redirect: sinon.stub(),
      };
      const mockNext = sinon.stub();
      return { mockReq, mockRes, mockNext };
    }

    it('GET /signIn handler should call samlController.signInViaSAML', async () => {
      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/signIn', 'get');
      expect(handler).to.not.be.undefined;

      const mockSamlController = container.get<any>('SamlController');
      mockSamlController.signInViaSAML = sinon.stub().resolves();

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockSamlController.signInViaSAML.calledOnce).to.be.true;
    });

    it('GET /signIn handler should call next on error', async () => {
      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/signIn', 'get');

      const mockSamlController = container.get<any>('SamlController');
      mockSamlController.signInViaSAML = sinon.stub().rejects(new Error('SAML error'));

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('POST /signIn/callback handler should call next when user is missing', async () => {
      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/signIn/callback', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.user = undefined;
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('POST /signIn/callback handler should call next when no session token in relay state', async () => {
      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/signIn/callback', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1' })).toString('base64');
      mockReq.body.RelayState = relayState;
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('POST /updateAppConfig handler should update config and respond 200', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config');
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').resolves(mockConfig as any);

      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/updateAppConfig', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockRes.status.calledWith(200)).to.be.true;
      expect(mockRes.json.calledOnce).to.be.true;
      const response = mockRes.json.firstCall.args[0];
      expect(response.message).to.include('updated successfully');

      loadStub.restore();
    });

    it('POST /updateAppConfig handler should call next on error', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config');
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').rejects(new Error('Config error'));

      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/updateAppConfig', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;

      loadStub.restore();
    });
  });
});
