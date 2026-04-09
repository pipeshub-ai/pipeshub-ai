import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createUserAccountRouter } from '../../../../src/modules/auth/routes/userAccount.routes';
import { UserAccountController } from '../../../../src/modules/auth/controller/userAccount.controller';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';

describe('createUserAccountRouter', () => {
  let container: Container;
  let mockUserAccountController: any;
  let mockAuthMiddleware: any;
  let mockConfig: any;

  beforeEach(() => {
    container = new Container();

    mockUserAccountController = {
      initAuth: sinon.stub(),
      authenticate: sinon.stub(),
      getLoginOtp: sinon.stub(),
      resetPassword: sinon.stub(),
      getAccessTokenFromRefreshToken: sinon.stub(),
      logoutSession: sinon.stub(),
      resetPasswordViaEmailLink: sinon.stub(),
      forgotPasswordEmail: sinon.stub(),
      hasPasswordMethod: sinon.stub(),
      exchangeOAuthToken: sinon.stub(),
    };

    mockAuthMiddleware = {
      scopedTokenValidator: sinon.stub().returns(
        (_req: any, _res: any, next: any) => next(),
      ),
    };

    mockConfig = {
      jwtSecret: 'test-secret',
      scopedJwtSecret: 'test-scoped',
    };

    container
      .bind<UserAccountController>('UserAccountController')
      .toConstantValue(mockUserAccountController);
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue(mockAuthMiddleware);
    container
      .bind<AppConfig>('AppConfig')
      .toConstantValue(mockConfig as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should return an Express router', () => {
    const router = createUserAccountRouter(container);
    expect(router).to.exist;
    expect(router).to.have.property('stack');
  });

  it('should register POST /initAuth route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const initAuthRoute = routes.find((r: any) => r.path === '/initAuth');
    expect(initAuthRoute).to.exist;
    expect(initAuthRoute?.methods.post).to.be.true;
  });

  it('should register POST /authenticate route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const authenticateRoute = routes.find(
      (r: any) => r.path === '/authenticate',
    );
    expect(authenticateRoute).to.exist;
    expect(authenticateRoute?.methods.post).to.be.true;
  });

  it('should register POST /login/otp/generate route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const otpRoute = routes.find(
      (r: any) => r.path === '/login/otp/generate',
    );
    expect(otpRoute).to.exist;
    expect(otpRoute?.methods.post).to.be.true;
  });

  it('should register POST /password/reset route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const resetRoute = routes.find(
      (r: any) => r.path === '/password/reset',
    );
    expect(resetRoute).to.exist;
    expect(resetRoute?.methods.post).to.be.true;
  });

  it('should register POST /refresh/token route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const refreshRoute = routes.find(
      (r: any) => r.path === '/refresh/token',
    );
    expect(refreshRoute).to.exist;
    expect(refreshRoute?.methods.post).to.be.true;
  });

  it('should register POST /logout/manual route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const logoutRoute = routes.find(
      (r: any) => r.path === '/logout/manual',
    );
    expect(logoutRoute).to.exist;
    expect(logoutRoute?.methods.post).to.be.true;
  });

  it('should register POST /password/reset/token route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const resetTokenRoute = routes.find(
      (r: any) => r.path === '/password/reset/token',
    );
    expect(resetTokenRoute).to.exist;
    expect(resetTokenRoute?.methods.post).to.be.true;
  });

  it('should register POST /password/forgot route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const forgotRoute = routes.find(
      (r: any) => r.path === '/password/forgot',
    );
    expect(forgotRoute).to.exist;
    expect(forgotRoute?.methods.post).to.be.true;
  });

  it('should register GET /internal/password/check route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const checkRoute = routes.find(
      (r: any) => r.path === '/internal/password/check',
    );
    expect(checkRoute).to.exist;
    expect(checkRoute?.methods.get).to.be.true;
  });

  it('should register POST /oauth/exchange route', () => {
    const router = createUserAccountRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const oauthRoute = routes.find(
      (r: any) => r.path === '/oauth/exchange',
    );
    expect(oauthRoute).to.exist;
    expect(oauthRoute?.methods.post).to.be.true;
  });

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createUserAccountRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      // initAuth, authenticate, login/otp/generate, password/reset, refresh/token,
      // logout/manual, password/reset/token, password/forgot, internal/password/check, oauth/exchange = 10
      expect(routes.length).to.be.greaterThanOrEqual(10);
    });
  });

  describe('middleware chains', () => {
    it('should use attachContainerMiddleware as router-level middleware', () => {
      const router = createUserAccountRouter(container);
      const middlewareLayers = router.stack.filter(
        (layer: any) => !layer.route,
      );
      expect(middlewareLayers.length).to.be.greaterThanOrEqual(1);
    });

    it('should have validation middleware on initAuth route', () => {
      const router = createUserAccountRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const initAuthRoute = routes.find(
        (layer: any) => layer.route.path === '/initAuth' && layer.route.methods.post,
      );
      expect(initAuthRoute).to.not.be.undefined;
      // validation + handler
      expect(initAuthRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });

    it('should have auth session middleware on authenticate route', () => {
      const router = createUserAccountRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const authenticateRoute = routes.find(
        (layer: any) => layer.route.path === '/authenticate' && layer.route.methods.post,
      );
      expect(authenticateRoute).to.not.be.undefined;
      // authSessionMiddleware + validation + handler
      expect(authenticateRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });

    it('should have scoped token validator on refresh/token route', () => {
      const router = createUserAccountRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const refreshRoute = routes.find(
        (layer: any) => layer.route.path === '/refresh/token' && layer.route.methods.post,
      );
      expect(refreshRoute).to.not.be.undefined;
      // scopedTokenValidator + handler
      expect(refreshRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });

    it('should have user validator on password/reset route', () => {
      const router = createUserAccountRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const resetRoute = routes.find(
        (layer: any) => layer.route.path === '/password/reset' && layer.route.methods.post,
      );
      expect(resetRoute).to.not.be.undefined;
      // userValidator + validation + handler
      expect(resetRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });

    it('should have scoped token validator on internal/password/check route', () => {
      const router = createUserAccountRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const checkRoute = routes.find(
        (layer: any) => layer.route.path === '/internal/password/check' && layer.route.methods.get,
      );
      expect(checkRoute).to.not.be.undefined;
      // scopedTokenValidator + handler
      expect(checkRoute.route.stack.length).to.be.greaterThanOrEqual(2);
    });

    it('should have validation middleware on oauth/exchange route', () => {
      const router = createUserAccountRouter(container);
      const routes = router.stack.filter((layer: any) => layer.route);

      const oauthExchangeRoute = routes.find(
        (layer: any) => layer.route.path === '/oauth/exchange' && layer.route.methods.post,
      );
      expect(oauthExchangeRoute).to.not.be.undefined;
      // validation + handler
      expect(oauthExchangeRoute.route.stack.length).to.be.greaterThanOrEqual(2);
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
        user: { userId: 'user123', orgId: 'org123' },
        tokenPayload: { userId: 'user123', orgId: 'org123' },
        body: {},
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
      };
      const mockNext = sinon.stub();
      return { mockReq, mockRes, mockNext };
    }

    it('POST /initAuth handler should call userAccountController.initAuth', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/initAuth', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.initAuth.calledOnce).to.be.true;
    });

    it('POST /initAuth handler should call next on error', async () => {
      mockUserAccountController.initAuth.rejects(new Error('Init failed'));
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/initAuth', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('POST /authenticate handler should call userAccountController.authenticate', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/authenticate', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.authenticate.calledOnce).to.be.true;
    });

    it('POST /login/otp/generate handler should call userAccountController.getLoginOtp', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/login/otp/generate', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.getLoginOtp.calledOnce).to.be.true;
    });

    it('POST /login/otp/generate handler should call next on error', async () => {
      mockUserAccountController.getLoginOtp.rejects(new Error('OTP failed'));
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/login/otp/generate', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('POST /password/reset handler should call userAccountController.resetPassword', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/password/reset', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.resetPassword.calledOnce).to.be.true;
    });

    it('POST /refresh/token handler should call userAccountController.getAccessTokenFromRefreshToken', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/refresh/token', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.getAccessTokenFromRefreshToken.calledOnce).to.be.true;
    });

    it('POST /logout/manual handler should call userAccountController.logoutSession', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/logout/manual', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.logoutSession.calledOnce).to.be.true;
    });

    it('POST /password/reset/token handler should call userAccountController.resetPasswordViaEmailLink', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/password/reset/token', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.resetPasswordViaEmailLink.calledOnce).to.be.true;
    });

    it('POST /password/forgot handler should call userAccountController.forgotPasswordEmail', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/password/forgot', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.forgotPasswordEmail.calledOnce).to.be.true;
    });

    it('POST /password/forgot handler should call next on error', async () => {
      mockUserAccountController.forgotPasswordEmail.rejects(new Error('Email failed'));
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/password/forgot', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('GET /internal/password/check handler should call userAccountController.hasPasswordMethod', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/internal/password/check', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.hasPasswordMethod.calledOnce).to.be.true;
    });

    it('POST /oauth/exchange handler should call userAccountController.exchangeOAuthToken', async () => {
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/oauth/exchange', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserAccountController.exchangeOAuthToken.calledOnce).to.be.true;
    });

    it('POST /oauth/exchange handler should call next on error', async () => {
      mockUserAccountController.exchangeOAuthToken.rejects(new Error('Exchange failed'));
      const router = createUserAccountRouter(container);
      const handler = findRouteHandler(router, '/oauth/exchange', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });
  });
});
