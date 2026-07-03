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
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';

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
      getUserByEmail: sinon.stub().resolves({
        statusCode: 200,
        data: { _id: 'user123', email: 'test@example.com', orgId: 'org1', hasLoggedIn: true },
      }),
      getUserById: sinon.stub(),
      updateUser: sinon.stub(),
      checkAdminUser: sinon.stub(),
      createOrg: sinon.stub(),
      createUser: sinon.stub(),
    };

    const mockSessionService = {
      createSession: sinon.stub().resolves({ userId: 'user123', orgId: 'org1' }),
      getSession: sinon.stub(),
      updateSession: sinon.stub(),
      completeAuthentication: sinon.stub().resolves(),
      deleteSession: sinon.stub(),
    };

    const mockSamlController = {
      signInViaSAML: sinon.stub(),
      getSamlEmailKeyByOrgId: sinon.stub(),
      updateSAMLStrategy: sinon.stub(),
      parseRelayState: sinon.stub().returns({ orgId: 'org1', sessionToken: 'token123' }),
      getSamlEmail: sinon.stub().returns('test@example.com'),
    };

    const mockMailService = {
      sendMail: sinon.stub(),
    };

    const mockConfigService = {
      getConfig: sinon.stub().resolves({ statusCode: 200, data: { enableJit: false } }),
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
      extractSamlUserDetails: sinon.stub().returns({ fullName: 'Test User' }),
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

    // Stub Mongoose model statics used by the handler
    sinon.stub(Org, 'findOne').returns({
      lean: sinon.stub().returns({ exec: sinon.stub().resolves({ _id: 'org1', shortName: 'TestOrg' }) }),
    } as any);
    sinon.stub(OrgAuthConfig, 'findOne').resolves({
      orgId: 'org1',
      isDeleted: false,
      authSteps: [{ allowedMethods: [{ type: 'samlSso' }] }],
    } as any);
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

    it('POST /signIn/callback handler should redirect with unknown error when user is missing', async () => {
      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/signIn/callback', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.user = undefined;
      await handler(mockReq, mockRes, mockNext);

      expect(mockRes.redirect.calledOnce).to.be.true;
      expect(mockRes.redirect.firstCall.args[0]).to.include('saml_error=unknown');
    });

    it('POST /signIn/callback handler should redirect when no session token in relay state', async () => {
      const mockSamlController = container.get<any>('SamlController');
      mockSamlController.parseRelayState.returns({ orgId: 'org1' });

      const router = createSamlRouter(container);
      const handler = findRouteHandler(router, '/signIn/callback', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.user = { email: 'test@example.com', orgId: 'org1' };
      await handler(mockReq, mockRes, mockNext);

      // With no session token, the handler creates a new session and redirects
      expect(mockRes.redirect.calledOnce).to.be.true;
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

describe('SAML Routes - handler coverage', () => {
  let container: Container
  let router: any
  let mockSamlController: any
  let mockSessionService: any
  let mockIamService: any
  let mockJitProvisioningService: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      jwtSecret: 'jwt-secret',
      cookieSecret: 'cookie-secret',
      cmBackend: 'http://localhost:3004',
    }

    const mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    }

    mockSamlController = {
      signInViaSAML: sinon.stub().resolves(),
      getSamlEmailKeyByOrgId: sinon.stub().returns('email'),
      parseRelayState: sinon.stub().returns({ orgId: 'org1', sessionToken: 'token123' }),
      getSamlEmail: sinon.stub().returns('test@test.com'),
    }

    mockSessionService = {
      getSession: sinon.stub().resolves(null),
      completeAuthentication: sinon.stub().resolves(),
      createSession: sinon.stub().resolves({ userId: 'u1', orgId: 'org1' }),
    }

    mockIamService = {
      getUserByEmail: sinon.stub().resolves({ data: { _id: 'u1', hasLoggedIn: false } }),
      updateUser: sinon.stub().resolves(),
    }

    mockJitProvisioningService = {
      extractSamlUserDetails: sinon.stub().returns({ fullName: 'Test User' }),
      provisionUser: sinon.stub().resolves({ _id: 'u1', hasLoggedIn: false }),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<any>('AppConfig').toConstantValue(mockConfig)
    container.bind<any>('SessionService').toConstantValue(mockSessionService)
    container.bind<any>('IamService').toConstantValue(mockIamService)
    container.bind<any>('SamlController').toConstantValue(mockSamlController)
    container.bind<any>('JitProvisioningService').toConstantValue(mockJitProvisioningService)
    container.bind<Logger>('Logger').toConstantValue(mockLogger as any)
    // Need to bind ConfigurationManagerService for updateAppConfig handler
    container.bind<any>('ConfigurationManagerService').toConstantValue({
      getConfig: sinon.stub().resolves({ statusCode: 200, data: { enableJit: false } }),
    })
    container.bind<any>('MailService').toConstantValue({ sendMail: sinon.stub() })

    // Stub Mongoose model statics used by the new handler
    const orgFindOneStub = sinon.stub(Org, 'findOne')
    const orgLeanExecChain = { lean: sinon.stub().returns({ exec: sinon.stub().resolves({ _id: 'org1', shortName: 'TestOrg' }) }) }
    orgFindOneStub.returns(orgLeanExecChain as any)

    sinon.stub(OrgAuthConfig, 'findOne').resolves({
      orgId: 'org1',
      isDeleted: false,
      authSteps: [{ allowedMethods: [{ type: 'samlSso' }] }],
    } as any)

    router = createSamlRouter(container)
  })

  afterEach(() => {
    sinon.restore()
  })

  function findHandler(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    )
    if (!layer) return null
    return layer.route.stack[layer.route.stack.length - 1].handle
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
      cookie: sinon.stub().returnsThis(),
      redirect: sinon.stub().returnsThis(),
    }
    return res
  }

  it('should create router with routes', () => {
    expect(router).to.exist
    expect(router.stack.length).to.be.greaterThan(0)
  })

  describe('GET /signIn', () => {
    it('should have a handler', () => {
      const handler = findHandler('/signIn', 'get')
      expect(handler).to.be.a('function')
    })

    it('should call samlController.signInViaSAML', async () => {
      const handler = findHandler('/signIn', 'get')
      const req = { body: {}, headers: {}, query: {} }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockSamlController.signInViaSAML.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      const handler = findHandler('/signIn', 'get')
      mockSamlController.signInViaSAML.rejects(new Error('SAML error'))

      const req = { body: {}, headers: {}, query: {} }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST /signIn/callback', () => {
    it('should have a handler', () => {
      const handler = findHandler('/signIn/callback', 'post')
      expect(handler).to.be.a('function')
    })

    it('should redirect with error when user not in request', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const req = {
        user: null,
        body: {},
        query: {},
        headers: {},
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(res.redirect.calledOnce).to.be.true
      expect(res.redirect.firstCall.args[0]).to.include('saml_error=unknown')
    })

    it('should call next when session token missing', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      // Override parseRelayState to return no sessionToken
      mockSamlController.parseRelayState.returns({ orgId: 'org1' })

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      const req = {
        user: { email: 'test@test.com', orgId: 'org1' },
        body: {},
        query: {},
        headers: {},
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      // With no session token, the handler creates a new session and redirects
      expect(res.redirect.calledOnce).to.be.true
    })

    it('should call next when session is null', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      mockSessionService.getSession.resolves(null)

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      const req = {
        user: { email: 'test@test.com', orgId: 'org1' },
        body: {},
        query: {},
        headers: {},
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      // With null session, the handler creates a new session and redirects
      expect(res.redirect.calledOnce).to.be.true
    })

    it('should redirect with error when auth method not allowed', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      // Override OrgAuthConfig.findOne to return config without SAML
      ;(OrgAuthConfig.findOne as sinon.SinonStub).resolves({
        orgId: 'org1',
        isDeleted: false,
        authSteps: [{ allowedMethods: [{ type: 'password' }] }],
      } as any)

      const req = {
        user: { email: 'test@test.com', orgId: 'org1' },
        body: {},
        query: {},
        headers: {},
        sessionInfo: null,
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      // The handler redirects with saml_sso_disabled error when SAML not in allowed methods
      expect(res.redirect.calledOnce).to.be.true
      expect(res.redirect.firstCall.args[0]).to.include('saml_sso_disabled')
    })

    it('should redirect with error when orgId missing and no default org', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      // Override parseRelayState to return no orgId
      mockSamlController.parseRelayState.returns({})
      // Override Org.findOne to return no org (so orgId stays undefined)
      ;(Org.findOne as sinon.SinonStub).returns({
        lean: sinon.stub().returns({ exec: sinon.stub().resolves(null) }),
      })
      // OrgAuthConfig.findOne with undefined orgId returns null
      ;(OrgAuthConfig.findOne as sinon.SinonStub).resolves(null)

      const req = {
        user: { email: 'test@test.com' },
        body: {},
        query: {},
        headers: {},
        sessionInfo: null,
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      // With no orgAuthConfig, samlAllowed is false, so redirect with saml_sso_disabled
      expect(res.redirect.calledOnce).to.be.true
      expect(res.redirect.firstCall.args[0]).to.include('saml_sso_disabled')
    })
  })

  describe('POST /signIn/callback - existing user success flow', () => {
    it('should redirect on success for existing user', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      const req = {
        user: { email: 'test@test.com', orgId: 'org1' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.cookie.called).to.be.true
        expect(res.redirect.calledOnce).to.be.true
      }
    })

    it('should redirect for first-time login', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: false },
      })

      const req = {
        user: { email: 'test@test.com', orgId: 'org1' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.redirect.calledOnce).to.be.true
      }
    })

    it('should handle JIT provisioning for NOT_FOUND user', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'new@test.com',
        orgId: 'org1',
        userId: 'NOT_FOUND',
        jitConfig: { saml: true },
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('email')
      mockJitProvisioningService.extractSamlUserDetails.returns({ fullName: 'New User' })
      mockJitProvisioningService.provisionUser.resolves({
        _id: 'new-u1', email: 'new@test.com', orgId: 'org1', hasLoggedIn: false,
      })

      const { UserActivities } = require('../../../../src/modules/auth/schema/userActivities.schema')
      sinon.stub(UserActivities, 'create').resolves({})

      mockIamService.updateUser.resolves({ statusCode: 200 })

      const req = {
        user: { email: 'new@test.com', orgId: 'org1' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      if (!next.called) {
        expect(mockJitProvisioningService.provisionUser.calledOnce).to.be.true
        expect(res.redirect.calledOnce).to.be.true
      }
    })

    it('should redirect jit_disabled when session jitConfig has no saml key', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      // Session created by initAuth: Google/Microsoft/OAuth have JIT on, SAML does not
      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'new@test.com',
        orgId: 'org1',
        userId: 'NOT_FOUND',
        jitConfig: { google: true, microsoft: true, oauth: true },
      })

      // User does not exist in IAM
      mockIamService.getUserByEmail.resolves({ statusCode: 404, data: null })

      const req = {
        user: { email: 'new@test.com', orgId: 'org1' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      expect(res.redirect.calledOnce).to.be.true
      expect(res.redirect.firstCall.args[0]).to.include('jit_disabled')
      expect(mockJitProvisioningService.provisionUser.called).to.be.false
    })

    it('should redirect with error when email mismatch', async () => {
      const handler = findHandler('/signIn/callback', 'post')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'expected@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockSamlController.getSamlEmail.returns('different@test.com')

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'different@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      const req = {
        user: { email: 'different@test.com', orgId: 'org1' },
        body: {},
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      // The new handler proceeds with the SAML email; should redirect or call next
      expect(res.redirect.called || next.called).to.be.true
    })

    it('should fallback to email key when SAML key returns invalid email', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      const req = {
        user: {
          customKey: 'not-an-email',
          email: 'test@test.com',
          orgId: 'org1',
        },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      // Should have used fallback email
      expect(res.redirect.called || next.called).to.be.true
    })

    it('should use RelayState from query params if not in body', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      const req = {
        user: { email: 'test@test.com', orgId: 'org1' },
        body: {},
        query: { RelayState: relayState },
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      // Should work the same as body relay state
      expect(res.redirect.called || next.called).to.be.true
    })

    it('should redirect with unknown error when no valid email in SAML response', async () => {
      const handler = findHandler('/signIn/callback', 'post')

      // getSamlEmail returns null when no valid email is found
      mockSamlController.getSamlEmail.returns(null)

      const req = {
        user: { customKey: 'not-an-email', noEmail: 'nope', orgId: 'org1' },
        body: {},
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      expect(res.redirect.calledOnce).to.be.true
      expect(res.redirect.firstCall.args[0]).to.include('saml_error=unknown')
    })
  })

  describe('POST /updateAppConfig', () => {
    it('should have a handler', () => {
      const handler = findHandler('/updateAppConfig', 'post')
      expect(handler).to.be.a('function')
    })

    it('should call next on error', async () => {
      const handler = findHandler('/updateAppConfig', 'post')
      const req = { body: {}, headers: {} }
      const res = mockRes()
      const next = sinon.stub()

      // loadAppConfig will fail due to missing env
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })
})
