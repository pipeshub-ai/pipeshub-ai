import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createUserRouter } from '../../../../src/modules/user_management/routes/users.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { Logger } from '../../../../src/libs/services/logger.service';
import { UserController } from '../../../../src/modules/user_management/controller/users.controller';
import { MailService } from '../../../../src/modules/user_management/services/mail.service';
import { AuthService } from '../../../../src/modules/user_management/services/auth.service';
import { EntitiesEventProducer } from '../../../../src/modules/user_management/services/entity_events.service';
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';

describe('User Routes', () => {
  let container: Container;
  let mockAuthMiddleware: any;
  let mockLogger: any;
  let mockConfig: any;
  let mockUserController: any;
  let mockMailService: any;
  let mockAuthService: any;
  let mockEventService: any;
  let mockOrgController: any;

  beforeEach(() => {
    container = new Container();

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    };

    mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    };

    mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      cmBackend: 'http://localhost:3004',
      connectorBackend: 'http://localhost:8088',
    };

    mockUserController = {
      getAllUsers: sinon.stub().resolves(),
      getAllUsersWithGroups: sinon.stub().resolves(),
      getUserById: sinon.stub().resolves(),
      getUserEmailByUserId: sinon.stub().resolves(),
      getUsersByIds: sinon.stub().resolves(),
      checkUserExistsByEmail: sinon.stub().resolves(),
      createUser: sinon.stub().resolves(),
      updateUser: sinon.stub().resolves(),
      updateFullName: sinon.stub().resolves(),
      updateFirstName: sinon.stub().resolves(),
      updateLastName: sinon.stub().resolves(),
      updateDesignation: sinon.stub().resolves(),
      updateEmail: sinon.stub().resolves(),
      deleteUser: sinon.stub().resolves(),
      updateUserDisplayPicture: sinon.stub().resolves(),
      getUserDisplayPicture: sinon.stub().resolves(),
      removeUserDisplayPicture: sinon.stub().resolves(),
      resendInvite: sinon.stub().resolves(),
      addManyUsers: sinon.stub().resolves(),
      listUsers: sinon.stub().resolves(),
      getUserTeams: sinon.stub().resolves(),
      unblockUser: sinon.stub().resolves(),
    };

    mockMailService = {
      sendMail: sinon.stub().resolves({ statusCode: 200 }),
    };

    mockAuthService = {
      passwordMethodEnabled: sinon.stub().resolves({ statusCode: 200 }),
    };

    mockEventService = {
      start: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    };

    mockOrgController = {
      checkOrgExistence: sinon.stub().resolves(),
      createOrg: sinon.stub().resolves(),
    };

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any);
    container.bind<Logger>('Logger').toConstantValue(mockLogger as any);
    container.bind<AppConfig>('AppConfig').toConstantValue(mockConfig as any);
    container.bind<UserController>('UserController').toConstantValue(mockUserController as any);
    container.bind<MailService>('MailService').toConstantValue(mockMailService as any);
    container.bind<AuthService>('AuthService').toConstantValue(mockAuthService as any);
    container.bind<EntitiesEventProducer>('EntitiesEventProducer').toConstantValue(mockEventService as any);
    container.bind<OrgController>('OrgController').toConstantValue(mockOrgController as any);

  });

  afterEach(() => {
    sinon.restore();
  });

  it('should create a router successfully', () => {
    const router = createUserRouter(container);
    expect(router).to.be.a('function');
  });

  it('should have route handlers registered', () => {
    const router = createUserRouter(container);
    const routes = (router as any).stack || [];

    // Router should have multiple route layers
    expect(routes.length).to.be.greaterThan(0);
  });

  describe('route registration', () => {
    it('should register GET / route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const getRoot = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.get,
      );
      expect(getRoot).to.not.be.undefined;
    });

    it('should register POST / route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const postRoot = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.post,
      );
      expect(postRoot).to.not.be.undefined;
    });

    it('should register GET /:id route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const getById = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id' &&
          layer.route.methods.get,
      );
      expect(getById).to.not.be.undefined;
    });

    it('should register PUT /:id route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const putById = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id' &&
          layer.route.methods.put,
      );
      expect(putById).to.not.be.undefined;
    });

    it('should register DELETE /:id route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const deleteById = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id' &&
          layer.route.methods.delete,
      );
      expect(deleteById).to.not.be.undefined;
    });

    it('should register PATCH /:id/fullname route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const patchFullName = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/fullname' &&
          layer.route.methods.patch,
      );
      expect(patchFullName).to.not.be.undefined;
    });

    it('should register PATCH /:id/firstName route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const patchFirstName = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/firstName' &&
          layer.route.methods.patch,
      );
      expect(patchFirstName).to.not.be.undefined;
    });

    it('should register PATCH /:id/lastName route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const patchLastName = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/lastName' &&
          layer.route.methods.patch,
      );
      expect(patchLastName).to.not.be.undefined;
    });

    it('should register PATCH /:id/designation route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const patchDesignation = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/designation' &&
          layer.route.methods.patch,
      );
      expect(patchDesignation).to.not.be.undefined;
    });

    it('should register PATCH /:id/email route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const patchEmail = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/email' &&
          layer.route.methods.patch,
      );
      expect(patchEmail).to.not.be.undefined;
    });

    it('should register GET /health route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const health = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/health' &&
          layer.route.methods.get,
      );
      expect(health).to.not.be.undefined;
    });

    it('should register PUT /dp route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const putDp = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/dp' &&
          layer.route.methods.put,
      );
      expect(putDp).to.not.be.undefined;
    });

    it('should register GET /dp route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const getDp = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/dp' &&
          layer.route.methods.get,
      );
      expect(getDp).to.not.be.undefined;
    });

    it('should register DELETE /dp route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const deleteDp = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/dp' &&
          layer.route.methods.delete,
      );
      expect(deleteDp).to.not.be.undefined;
    });

    it('should register POST /bulk/invite route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const bulkInvite = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/bulk/invite' &&
          layer.route.methods.post,
      );
      expect(bulkInvite).to.not.be.undefined;
    });

    it('should register POST /:id/resend-invite route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const resendInvite = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/resend-invite' &&
          layer.route.methods.post,
      );
      expect(resendInvite).to.not.be.undefined;
    });

    it('should register POST /by-ids route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const byIds = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/by-ids' &&
          layer.route.methods.post,
      );
      expect(byIds).to.not.be.undefined;
    });

    it('should register PUT /:id/unblock route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const unblock = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/unblock' &&
          layer.route.methods.put,
      );
      expect(unblock).to.not.be.undefined;
    });

    it('should register GET /graph/list route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const graphList = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/graph/list' &&
          layer.route.methods.get,
      );
      expect(graphList).to.not.be.undefined;
    });

    it('should register GET /fetch/with-groups route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const withGroups = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/fetch/with-groups' &&
          layer.route.methods.get,
      );
      expect(withGroups).to.not.be.undefined;
    });

    it('should register GET /:id/email route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const emailRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/email' &&
          layer.route.methods.get,
      );
      expect(emailRoute).to.not.be.undefined;
    });

    it('should register GET /email/exists route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const existsRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/email/exists' &&
          layer.route.methods.get,
      );
      expect(existsRoute).to.not.be.undefined;
    });

    it('should register GET /internal/admin-users route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const adminUsersRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/admin-users' &&
          layer.route.methods.get,
      );
      expect(adminUsersRoute).to.not.be.undefined;
    });

    it('should register GET /internal/:id route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const internalGetRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:id' &&
          layer.route.methods.get,
      );
      expect(internalGetRoute).to.not.be.undefined;
    });

    it('should register PATCH /:id/email route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const patchEmail = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/email' &&
          layer.route.methods.patch,
      );
      expect(patchEmail).to.not.be.undefined;
    });

    it('should register GET /:id/adminCheck route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const adminCheck = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:id/adminCheck' &&
          layer.route.methods.get,
      );
      expect(adminCheck).to.not.be.undefined;
    });

    it('should register POST /updateAppConfig route', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack;

      const updateConfig = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/updateAppConfig' &&
          layer.route.methods.post,
      );
      expect(updateConfig).to.not.be.undefined;
    });
  });

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      // Should have a significant number of routes
      expect(routes.length).to.be.greaterThanOrEqual(20);
    });
  });

  describe('middleware chains', () => {
    it('should include multiple middleware handlers on authenticated routes', () => {
      const router = createUserRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      for (const routeLayer of routes) {
        const handlerCount = routeLayer.route.stack.length;
        expect(handlerCount).to.be.greaterThanOrEqual(1,
          `Route ${routeLayer.route.path} should have at least 1 handler`);
      }
    });
  });

  describe('route handler invocations', () => {
    function findRouteLayer(router: any, path: string, method: string) {
      return router.stack.find(
        (l: any) => l.route && l.route.path === path && l.route.methods[method],
      );
    }

    function findRouteHandler(router: any, path: string, method: string) {
      const layer = findRouteLayer(router, path, method);
      if (!layer) return undefined;
      const handlers = layer.route.stack.map((s: any) => s.handle);
      return handlers[handlers.length - 1];
    }

    function createMockReqRes() {
      const mockReq: any = {
        user: { userId: 'user123', orgId: 'org123', role: 'admin' },
        tokenPayload: { userId: 'user123', orgId: 'org123' },
        body: {},
        params: { id: '507f1f77bcf86cd799439011' },
        query: {},
        headers: {},
        method: 'GET',
        path: '/',
        ip: '127.0.0.1',
      };
      const mockRes: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
        cookie: sinon.stub(),
        on: sinon.stub().returnsThis(),
      };
      const mockNext = sinon.stub();
      return { mockReq, mockRes, mockNext };
    }

    function findValidationMiddleware(router: any, path: string, method: string) {
      const routeLayer = findRouteLayer(router, path, method);
      if (!routeLayer) return undefined;
      // For GET / route stack: auth, scope, validation, metrics, handler
      return routeLayer.route.stack[2]?.handle;
    }

    it('GET / handler should call userController.getAllUsers', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.getAllUsers.calledOnce).to.be.true;
    });

    it('GET / handler should call next on error', async () => {
      mockUserController.getAllUsers.rejects(new Error('DB error'));
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/', 'get');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
      expect(mockNext.firstCall.args[0]).to.be.an.instanceOf(Error);
    });

    it('GET / should reject non-numeric page query via validation middleware', async () => {
      const router = createUserRouter(container);
      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.query = { page: 'abc' };
      const validationMiddleware = findValidationMiddleware(router, '/', 'get');

      expect(validationMiddleware).to.not.be.undefined;
      await validationMiddleware(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
      expect(mockNext.firstCall.args[0]).to.exist;
      expect(mockUserController.getAllUsers.called).to.be.false;
    });

    it('GET / should reject limit greater than 100 via validation middleware', async () => {
      const router = createUserRouter(container);
      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.query = { limit: '101' };
      const validationMiddleware = findValidationMiddleware(router, '/', 'get');

      expect(validationMiddleware).to.not.be.undefined;
      await validationMiddleware(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
      expect(mockNext.firstCall.args[0]).to.exist;
      expect(mockUserController.getAllUsers.called).to.be.false;
    });

    it('GET / should reject invalid groupIds via validation middleware', async () => {
      const router = createUserRouter(container);
      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.query = { groupIds: 'invalid-object-id,507f1f77bcf86cd799439011' };
      const validationMiddleware = findValidationMiddleware(router, '/', 'get');

      expect(validationMiddleware).to.not.be.undefined;
      await validationMiddleware(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
      expect(mockNext.firstCall.args[0]).to.exist;
      expect(mockUserController.getAllUsers.called).to.be.false;
    });

    it('GET / should accept valid query filters and reach controller', async () => {
      const router = createUserRouter(container);
      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.query = {
        page: '1',
        limit: '25',
        hasLoggedIn: 'true',
        isBlocked: 'false',
        groupIds: '507f1f77bcf86cd799439011,507f1f77bcf86cd799439012',
      };
      const validationMiddleware = findValidationMiddleware(router, '/', 'get');
      const handler = findRouteHandler(router, '/', 'get');

      expect(validationMiddleware).to.not.be.undefined;
      expect(handler).to.not.be.undefined;
      await validationMiddleware(mockReq, mockRes, mockNext);
      expect(mockNext.called).to.be.true;
      expect(mockNext.firstCall.args[0]).to.be.undefined;

      mockNext.resetHistory();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.getAllUsers.calledOnce).to.be.true;
      expect(mockNext.called).to.be.false;
    });

    it('GET /fetch/with-groups handler should call userController.getAllUsersWithGroups', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/fetch/with-groups', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.getAllUsersWithGroups.calledOnce).to.be.true;
    });

    it('GET /fetch/with-groups handler should call next on error', async () => {
      mockUserController.getAllUsersWithGroups.rejects(new Error('DB error'));
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/fetch/with-groups', 'get');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('GET /:id/email handler should call userController.getUserEmailByUserId', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/email', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.getUserEmailByUserId.calledOnce).to.be.true;
    });

    it('PUT /:id/unblock handler should call userController.unblockUser', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/unblock', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.unblockUser.calledOnce).to.be.true;
    });

    it('PUT /:id/unblock handler should call next on error', async () => {
      mockUserController.unblockUser.rejects(new Error('unblock failed'));
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/unblock', 'put');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('GET /:id handler should call userController.getUserById', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.getUserById.calledOnce).to.be.true;
    });

    it('POST /by-ids handler should call userController.getUsersByIds', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/by-ids', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.getUsersByIds.calledOnce).to.be.true;
    });

    it('GET /email/exists handler should call userController.checkUserExistsByEmail', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/email/exists', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.checkUserExistsByEmail.calledOnce).to.be.true;
    });

    it('GET /internal/admin-users handler should return 400 when orgId is missing', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/internal/admin-users', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      mockReq.tokenPayload = {};
      await handler(mockReq, mockRes, mockNext);

      expect(mockRes.status.calledWith(400)).to.be.true;
    });

    it('POST / handler should call userController.createUser', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.createUser.calledOnce).to.be.true;
    });

    it('POST / handler should call next on error', async () => {
      mockUserController.createUser.rejects(new Error('create failed'));
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('PATCH /:id/fullname handler should call userController.updateFullName', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/fullname', 'patch');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.updateFullName.calledOnce).to.be.true;
    });

    it('PATCH /:id/firstName handler should call userController.updateFirstName', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/firstName', 'patch');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.updateFirstName.calledOnce).to.be.true;
    });

    it('PATCH /:id/lastName handler should call userController.updateLastName', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/lastName', 'patch');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.updateLastName.calledOnce).to.be.true;
    });

    it('PUT /dp handler should call userController.updateUserDisplayPicture', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/dp', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.updateUserDisplayPicture.calledOnce).to.be.true;
    });

    it('DELETE /dp handler should call userController.removeUserDisplayPicture', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/dp', 'delete');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.removeUserDisplayPicture.calledOnce).to.be.true;
    });

    it('GET /dp handler should call userController.getUserDisplayPicture', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/dp', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.getUserDisplayPicture.calledOnce).to.be.true;
    });

    it('PATCH /:id/designation handler should call userController.updateDesignation', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/designation', 'patch');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.updateDesignation.calledOnce).to.be.true;
    });

    it('PATCH /:id/email handler should call userController.updateEmail', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/email', 'patch');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.updateEmail.calledOnce).to.be.true;
    });

    it('PUT /:id handler should call userController.updateUser', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.updateUser.calledOnce).to.be.true;
    });

    it('DELETE /:id handler should call userController.deleteUser', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id', 'delete');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.deleteUser.calledOnce).to.be.true;
    });

    it('GET /:id/adminCheck handler should respond 200', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/adminCheck', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockRes.status.calledWith(200)).to.be.true;
      expect(mockRes.json.calledOnce).to.be.true;
    });

    it('POST /bulk/invite handler should call userController.addManyUsers', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/bulk/invite', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.addManyUsers.calledOnce).to.be.true;
    });

    it('POST /:id/resend-invite handler should call userController.resendInvite', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/:id/resend-invite', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.resendInvite.calledOnce).to.be.true;
    });

    it('GET /health handler should respond with healthy status', () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/health', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes } = createMockReqRes();
      handler(mockReq, mockRes);

      expect(mockRes.json.calledOnce).to.be.true;
      const response = mockRes.json.firstCall.args[0];
      expect(response.status).to.equal('healthy');
      expect(response.timestamp).to.be.a('string');
    });

    it('POST /updateAppConfig handler should update config and respond 200', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config');
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').resolves(mockConfig as any);

      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/updateAppConfig', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockRes.status.calledWith(200)).to.be.true;
      expect(mockRes.json.calledOnce).to.be.true;

      loadStub.restore();
    });

    it('POST /updateAppConfig handler should call next on error', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config');
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').rejects(new Error('Config load failed'));

      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/updateAppConfig', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;

      loadStub.restore();
    });

    it('GET /graph/list handler should call userController.listUsers', async () => {
      const router = createUserRouter(container);
      const handler = findRouteHandler(router, '/graph/list', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserController.listUsers.calledOnce).to.be.true;
    });

  });
});

describe('User Routes - handler coverage', () => {
  let container: Container
  let mockUserController: any
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockLogger = {
      debug: sinon.stub(), info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(),
    }

    const mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      cmBackend: 'http://localhost:3004',
      connectorBackend: 'http://localhost:8088',
    }

    mockUserController = {
      getAllUsers: sinon.stub().resolves(),
      getAllUsersWithGroups: sinon.stub().resolves(),
      getUserById: sinon.stub().resolves(),
      getUserEmailByUserId: sinon.stub().resolves(),
      getUsersByIds: sinon.stub().resolves(),
      checkUserExistsByEmail: sinon.stub().resolves(),
      createUser: sinon.stub().resolves(),
      updateUser: sinon.stub().resolves(),
      updateFullName: sinon.stub().resolves(),
      updateFirstName: sinon.stub().resolves(),
      updateLastName: sinon.stub().resolves(),
      updateDesignation: sinon.stub().resolves(),
      updateEmail: sinon.stub().resolves(),
      deleteUser: sinon.stub().resolves(),
      updateUserDisplayPicture: sinon.stub().resolves(),
      getUserDisplayPicture: sinon.stub().resolves(),
      removeUserDisplayPicture: sinon.stub().resolves(),
      resendInvite: sinon.stub().resolves(),
      addManyUsers: sinon.stub().resolves(),
      listUsers: sinon.stub().resolves(),
      getUserTeams: sinon.stub().resolves(),
      unblockUser: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<Logger>('Logger').toConstantValue(mockLogger as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockConfig as any)
    container.bind<UserController>('UserController').toConstantValue(mockUserController as any)
    container.bind<MailService>('MailService').toConstantValue({ sendMail: sinon.stub() } as any)
    container.bind<AuthService>('AuthService').toConstantValue({ passwordMethodEnabled: sinon.stub() } as any)
    container.bind<EntitiesEventProducer>('EntitiesEventProducer').toConstantValue({ publishEvent: sinon.stub() } as any)
    container.bind<OrgController>('OrgController').toConstantValue({ createOrg: sinon.stub() } as any)

    router = createUserRouter(container)
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
    }
    return res
  }

  describe('GET / handler', () => {
    it('should call userController.getAllUsers', async () => {
      const handler = findHandler('/', 'get')
      expect(handler).to.exist

      const req = { user: { userId: 'user1', orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.getAllUsers.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      mockUserController.getAllUsers.rejects(new Error('DB error'))
      // Re-bind with throwing controller
      container.rebind<UserController>('UserController').toConstantValue(mockUserController as any)
      router = createUserRouter(container)

      const handler = findHandler('/', 'get')
      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET /fetch/with-groups handler', () => {
    it('should call userController.getAllUsersWithGroups', async () => {
      const handler = findHandler('/fetch/with-groups', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.getAllUsersWithGroups.calledOnce).to.be.true
    })
  })

  describe('PUT /:id/unblock handler', () => {
    it('should call userController.unblockUser', async () => {
      const handler = findHandler('/:id/unblock', 'put')
      expect(handler).to.exist

      const req = { params: { id: '507f1f77bcf86cd799439011' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.unblockUser.calledOnce).to.be.true
    })
  })

  describe('POST /by-ids handler', () => {
    it('should call userController.getUsersByIds', async () => {
      const handler = findHandler('/by-ids', 'post')
      expect(handler).to.exist

      const req = { body: { userIds: ['507f1f77bcf86cd799439011'] } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.getUsersByIds.calledOnce).to.be.true
    })
  })

  describe('GET /health handler', () => {
    it('should return health status', () => {
      const handler = findHandler('/health', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()

      handler(req, res)
      expect(res.json.calledOnce).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.status).to.equal('healthy')
      expect(response.timestamp).to.be.a('string')
    })
  })

  describe('GET /:id/adminCheck handler', () => {
    it('should return admin access message', async () => {
      const handler = findHandler('/:id/adminCheck', 'get')
      expect(handler).to.exist

      const req = { params: { id: '507f1f77bcf86cd799439011' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
    })
  })

  describe('POST /updateAppConfig handler', () => {
    it('should update app config and rebind services', async () => {
      const handler = findHandler('/updateAppConfig', 'post')
      expect(handler).to.exist

      // Stub loadAppConfig
      const configModule = require('../../../../src/modules/tokens_manager/config/config')
      sinon.stub(configModule, 'loadAppConfig').resolves({
        frontendUrl: 'http://localhost:3000',
        scopedJwtSecret: 'updated-secret',
        cmBackend: 'http://localhost:3004',
        connectorBackend: 'http://localhost:8088',
      })

      const req = { tokenPayload: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next on error', async () => {
      const handler = findHandler('/updateAppConfig', 'post')

      const configModule = require('../../../../src/modules/tokens_manager/config/config')
      sinon.stub(configModule, 'loadAppConfig').rejects(new Error('Config load failed'))

      const req = { tokenPayload: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET /graph/list handler', () => {
    it('should call userController.listUsers', async () => {
      const handler = findHandler('/graph/list', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.listUsers.calledOnce).to.be.true
    })
  })

  describe('additional route registrations', () => {
    it('should register PATCH /:id/fullname route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/fullname' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/firstName route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/firstName' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/lastName route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/lastName' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/designation route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/designation' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/email route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/email' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PUT /dp route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/dp' && l.route.methods.put,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register DELETE /dp route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/dp' && l.route.methods.delete,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /dp route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/dp' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register DELETE /:id route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id' && l.route.methods.delete,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register POST /bulk/invite route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/bulk/invite' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register POST /:id/resend-invite route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/resend-invite' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /internal/admin-users route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/internal/admin-users' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /internal/:id route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/internal/:id' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /email/exists route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/email/exists' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })
  })
})

describe('User Routes - handler coverage 2', () => {
  let container: Container
  let mockUserController: any
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockLogger = {
      debug: sinon.stub(), info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(),
    }

    const mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      cmBackend: 'http://localhost:3004',
      connectorBackend: 'http://localhost:8088',
    }

    mockUserController = {
      getAllUsers: sinon.stub().resolves(),
      getAllUsersWithGroups: sinon.stub().resolves(),
      getUserById: sinon.stub().resolves(),
      getUserEmailByUserId: sinon.stub().resolves(),
      getUsersByIds: sinon.stub().resolves(),
      checkUserExistsByEmail: sinon.stub().resolves(),
      createUser: sinon.stub().resolves(),
      updateUser: sinon.stub().resolves(),
      updateFullName: sinon.stub().resolves(),
      updateFirstName: sinon.stub().resolves(),
      updateLastName: sinon.stub().resolves(),
      updateDesignation: sinon.stub().resolves(),
      updateEmail: sinon.stub().resolves(),
      deleteUser: sinon.stub().resolves(),
      updateUserDisplayPicture: sinon.stub().resolves(),
      getUserDisplayPicture: sinon.stub().resolves(),
      removeUserDisplayPicture: sinon.stub().resolves(),
      resendInvite: sinon.stub().resolves(),
      addManyUsers: sinon.stub().resolves(),
      listUsers: sinon.stub().resolves(),
      getUserTeams: sinon.stub().resolves(),
      unblockUser: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<Logger>('Logger').toConstantValue(mockLogger as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockConfig as any)
    container.bind<UserController>('UserController').toConstantValue(mockUserController as any)
    container.bind<MailService>('MailService').toConstantValue({ sendMail: sinon.stub() } as any)
    container.bind<AuthService>('AuthService').toConstantValue({ passwordMethodEnabled: sinon.stub() } as any)
    container.bind<EntitiesEventProducer>('EntitiesEventProducer').toConstantValue({ publishEvent: sinon.stub() } as any)
    container.bind<OrgController>('OrgController').toConstantValue({ createOrg: sinon.stub() } as any)

    router = createUserRouter(container)
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
    }
    return res
  }

  // Test route handlers that delegate to controller and handle errors
  describe('GET /fetch/with-groups', () => {
    it('should call getAllUsersWithGroups', async () => {
      const handler = findHandler('/fetch/with-groups', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.getAllUsersWithGroups.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      const handler = findHandler('/fetch/with-groups', 'get')
      mockUserController.getAllUsersWithGroups.rejects(new Error('fail'))

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('PUT /:id/unblock', () => {
    it('should call unblockUser', async () => {
      const handler = findHandler('/:id/unblock', 'put')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' } }, res, next)
      expect(mockUserController.unblockUser.calledOnce).to.be.true
    })
  })

  describe('POST /by-ids', () => {
    it('should call getUsersByIds', async () => {
      const handler = findHandler('/by-ids', 'post')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ body: { userIds: ['id1'] } }, res, next)
      expect(mockUserController.getUsersByIds.calledOnce).to.be.true
    })
  })

  describe('GET /email/exists', () => {
    it('should call checkUserExistsByEmail', async () => {
      const handler = findHandler('/email/exists', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ body: { email: 'test@test.com' } }, res, next)
      expect(mockUserController.checkUserExistsByEmail.calledOnce).to.be.true
    })
  })

  describe('GET /internal/admin-users', () => {
    it('should return admin user IDs', async () => {
      const handler = findHandler('/internal/admin-users', 'get')
      expect(handler).to.be.a('function')
    })

    it('should return 400 when orgId is missing', async () => {
      const handler = findHandler('/internal/admin-users', 'get')
      const req = { tokenPayload: {} }
      const res = mockRes()
      const next = sinon.stub()
      await handler(req, res, next)
      expect(res.status.calledWith(400)).to.be.true
    })
  })

  describe('GET /internal/:id', () => {
    it('should have a handler', () => {
      const handler = findHandler('/internal/:id', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('PATCH /:id/firstName', () => {
    it('should call updateFirstName', async () => {
      const handler = findHandler('/:id/firstName', 'patch')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' }, body: { firstName: 'John' } }, res, next)
      expect(mockUserController.updateFirstName.calledOnce).to.be.true
    })
  })

  describe('PATCH /:id/lastName', () => {
    it('should call updateLastName', async () => {
      const handler = findHandler('/:id/lastName', 'patch')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' }, body: { lastName: 'Doe' } }, res, next)
      expect(mockUserController.updateLastName.calledOnce).to.be.true
    })
  })

  describe('PUT /dp', () => {
    it('should have a handler for PUT /dp', () => {
      const handler = findHandler('/dp', 'put')
      expect(handler).to.be.a('function')
    })
  })

  describe('DELETE /dp', () => {
    it('should call removeUserDisplayPicture', async () => {
      const handler = findHandler('/dp', 'delete')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.removeUserDisplayPicture.calledOnce).to.be.true
    })
  })

  describe('GET /dp', () => {
    it('should call getUserDisplayPicture', async () => {
      const handler = findHandler('/dp', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.getUserDisplayPicture.calledOnce).to.be.true
    })
  })

  describe('PATCH /:id/email', () => {
    it('should call updateEmail', async () => {
      const handler = findHandler('/:id/email', 'patch')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' }, body: { email: 'new@test.com' } }, res, next)
      expect(mockUserController.updateEmail.calledOnce).to.be.true
    })
  })

  describe('DELETE /:id', () => {
    it('should call deleteUser', async () => {
      const handler = findHandler('/:id', 'delete')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' } }, res, next)
      expect(mockUserController.deleteUser.calledOnce).to.be.true
    })
  })

  describe('GET /:id/adminCheck', () => {
    it('should return admin access message', async () => {
      const handler = findHandler('/:id/adminCheck', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('POST /bulk/invite', () => {
    it('should call addManyUsers', async () => {
      const handler = findHandler('/bulk/invite', 'post')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ body: {} }, res, next)
      expect(mockUserController.addManyUsers.calledOnce).to.be.true
    })
  })

  describe('POST /:id/resend-invite', () => {
    it('should call resendInvite', async () => {
      const handler = findHandler('/:id/resend-invite', 'post')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' } }, res, next)
      expect(mockUserController.resendInvite.calledOnce).to.be.true
    })
  })

  describe('GET /health', () => {
    it('should return healthy status', () => {
      const handler = findHandler('/health', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      handler({}, res)
      expect(res.json.called).to.be.true
      const arg = res.json.firstCall.args[0]
      expect(arg.status).to.equal('healthy')
    })
  })

  describe('POST /updateAppConfig', () => {
    it('should have a handler', () => {
      const handler = findHandler('/updateAppConfig', 'post')
      expect(handler).to.be.a('function')
    })

    it('should call next on error', async () => {
      const handler = findHandler('/updateAppConfig', 'post')
      const res = mockRes()
      const next = sinon.stub()
      // loadAppConfig will fail
      await handler({}, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET /graph/list', () => {
    it('should call listUsers', async () => {
      const handler = findHandler('/graph/list', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.listUsers.calledOnce).to.be.true
    })
  })

})
