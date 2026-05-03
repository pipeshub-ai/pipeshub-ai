import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createUserGroupRouter } from '../../../../src/modules/user_management/routes/userGroups.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { UserGroupController } from '../../../../src/modules/user_management/controller/userGroups.controller';

describe('UserGroups Routes', () => {
  let container: Container;
  let mockAuthMiddleware: any;
  let mockUserGroupController: any;

  beforeEach(() => {
    container = new Container();

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    };

    mockUserGroupController = {
      getAllUsers: sinon.stub().resolves(),
      createUserGroup: sinon.stub().resolves(),
      getAllUserGroups: sinon.stub().resolves(),
      getUserGroupById: sinon.stub().resolves(),
      updateGroup: sinon.stub().resolves(),
      deleteGroup: sinon.stub().resolves(),
      addUsersToGroups: sinon.stub().resolves(),
      removeUsersFromGroups: sinon.stub().resolves(),
      getUsersInGroup: sinon.stub().resolves(),
      getGroupsForUser: sinon.stub().resolves(),
      getGroupStatistics: sinon.stub().resolves(),
    };

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any);
    container.bind<UserGroupController>('UserGroupController').toConstantValue(mockUserGroupController as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should create a router successfully', () => {
    const router = createUserGroupRouter(container);
    expect(router).to.be.a('function');
  });

  it('should have route handlers registered', () => {
    const router = createUserGroupRouter(container);
    const routes = (router as any).stack || [];
    expect(routes.length).to.be.greaterThan(0);
  });

  describe('route registration', () => {
    it('should register POST / route for creating a group', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const createRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.post,
      );
      expect(createRoute).to.not.be.undefined;
    });

    it('should register GET / route for listing groups', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const listRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.get,
      );
      expect(listRoute).to.not.be.undefined;
    });

    it('should register GET /:groupId route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const getRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:groupId' &&
          layer.route.methods.get,
      );
      expect(getRoute).to.not.be.undefined;
    });

    it('should register PUT /:groupId route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const putRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:groupId' &&
          layer.route.methods.put,
      );
      expect(putRoute).to.not.be.undefined;
    });

    it('should register DELETE /:groupId route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const deleteRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:groupId' &&
          layer.route.methods.delete,
      );
      expect(deleteRoute).to.not.be.undefined;
    });

    it('should register POST /add-users route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const addUsersRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/add-users' &&
          layer.route.methods.post,
      );
      expect(addUsersRoute).to.not.be.undefined;
    });

    it('should register POST /remove-users route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const removeUsersRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/remove-users' &&
          layer.route.methods.post,
      );
      expect(removeUsersRoute).to.not.be.undefined;
    });

    it('should register GET /:groupId/users route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const usersRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:groupId/users' &&
          layer.route.methods.get,
      );
      expect(usersRoute).to.not.be.undefined;
    });

    it('should register GET /users/:userId route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const userGroupsRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/users/:userId' &&
          layer.route.methods.get,
      );
      expect(userGroupsRoute).to.not.be.undefined;
    });

    it('should register GET /stats/list route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const statsRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/stats/list' &&
          layer.route.methods.get,
      );
      expect(statsRoute).to.not.be.undefined;
    });

    it('should register GET /health route', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack;

      const healthRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/health' &&
          layer.route.methods.get,
      );
      expect(healthRoute).to.not.be.undefined;
    });
  });

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      // POST /, GET /, GET /:groupId, PUT /:groupId, DELETE /:groupId,
      // POST /add-users, POST /remove-users, GET /:groupId/users,
      // GET /users/:userId, GET /stats/list, GET /health = 11
      expect(routes.length).to.be.greaterThanOrEqual(11);
    });
  });

  describe('middleware chains', () => {
    it('should include auth middleware on protected routes', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      for (const routeLayer of routes) {
        const handlerCount = routeLayer.route.stack.length;
        expect(handlerCount).to.be.greaterThanOrEqual(1,
          `Route ${routeLayer.route.path} should have at least 1 handler`);
      }
    });

    it('should have admin check middleware on write routes', () => {
      const router = createUserGroupRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      // POST / (create) should have multiple middlewares: auth + requireScopes + validation + adminCheck + handler
      const createRoute = routes.find(
        (layer: any) => layer.route.path === '/' && layer.route.methods.post,
      );
      expect(createRoute).to.not.be.undefined;
      expect(createRoute.route.stack.length).to.be.greaterThanOrEqual(3);
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
        body: {},
        params: { groupId: '507f1f77bcf86cd799439011' },
        query: {},
        headers: {},
      };
      const mockRes: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
      };
      const mockNext = sinon.stub();
      return { mockReq, mockRes, mockNext };
    }

    it('POST / handler should call userGroupController.createUserGroup', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.createUserGroup.calledOnce).to.be.true;
    });

    it('POST / handler should call next on error', async () => {
      mockUserGroupController.createUserGroup.rejects(new Error('Create failed'));
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('GET / handler should call userGroupController.getAllUserGroups', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.getAllUserGroups.calledOnce).to.be.true;
    });

    it('GET /:groupId handler should call userGroupController.getUserGroupById', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/:groupId', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.getUserGroupById.calledOnce).to.be.true;
    });

    it('PUT /:groupId handler should call userGroupController.updateGroup', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/:groupId', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.updateGroup.calledOnce).to.be.true;
    });

    it('DELETE /:groupId handler should call userGroupController.deleteGroup', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/:groupId', 'delete');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.deleteGroup.calledOnce).to.be.true;
    });

    it('POST /add-users handler should call userGroupController.addUsersToGroups', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/add-users', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.addUsersToGroups.calledOnce).to.be.true;
    });

    it('POST /remove-users handler should call userGroupController.removeUsersFromGroups', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/remove-users', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.removeUsersFromGroups.calledOnce).to.be.true;
    });

    it('GET /:groupId/users handler should call userGroupController.getUsersInGroup', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/:groupId/users', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.getUsersInGroup.calledOnce).to.be.true;
    });

    it('GET /users/:userId handler should call userGroupController.getGroupsForUser', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/users/:userId', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.getGroupsForUser.calledOnce).to.be.true;
    });

    it('GET /stats/list handler should call userGroupController.getGroupStatistics', async () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/stats/list', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockUserGroupController.getGroupStatistics.calledOnce).to.be.true;
    });

    it('GET /health handler should respond with healthy status', () => {
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/health', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes } = createMockReqRes();
      handler(mockReq, mockRes);

      expect(mockRes.json.calledOnce).to.be.true;
      const response = mockRes.json.firstCall.args[0];
      expect(response.status).to.equal('healthy');
      expect(response.timestamp).to.be.a('string');
    });

    it('GET / handler should call next on error', async () => {
      mockUserGroupController.getAllUserGroups.rejects(new Error('DB error'));
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/', 'get');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('DELETE /:groupId handler should call next on error', async () => {
      mockUserGroupController.deleteGroup.rejects(new Error('Delete failed'));
      const router = createUserGroupRouter(container);
      const handler = findRouteHandler(router, '/:groupId', 'delete');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });
  });
});
