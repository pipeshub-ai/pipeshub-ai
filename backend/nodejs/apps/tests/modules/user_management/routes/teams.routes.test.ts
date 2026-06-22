import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createTeamsRouter } from '../../../../src/modules/user_management/routes/teams.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { TeamsController } from '../../../../src/modules/user_management/controller/teams.controller';
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service';

describe('Teams Routes', () => {
  let container: Container;
  let mockAuthMiddleware: any;
  let mockTeamsController: any;

  beforeEach(() => {
    container = new Container();

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    };

    mockTeamsController = {
      createTeam: sinon.stub().resolves(),
      getTeam: sinon.stub().resolves(),
      updateTeam: sinon.stub().resolves(),
      deleteTeam: sinon.stub().resolves(),
      getTeamUsers: sinon.stub().resolves(),
      getUserTeams: sinon.stub().resolves(),
    };

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any);
    container.bind<TeamsController>('TeamsController').toConstantValue(mockTeamsController as any);

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
    };
    container.bind<any>(PrometheusService).toConstantValue(mockPrometheusService);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should create a router successfully', () => {
    const router = createTeamsRouter(container);
    expect(router).to.be.a('function');
  });

  describe('route registration', () => {
    it('should register POST / route for creating team', () => {
      const router = createTeamsRouter(container);
      const routes = (router as any).stack;

      const createRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.post,
      );
      expect(createRoute).to.not.be.undefined;
    });

    it('should register GET /user/teams route', () => {
      const router = createTeamsRouter(container);
      const routes = (router as any).stack;

      const userTeamsRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/user/teams' &&
          layer.route.methods.get,
      );
      expect(userTeamsRoute).to.not.be.undefined;
    });

    it('should register GET /:teamId route', () => {
      const router = createTeamsRouter(container);
      const routes = (router as any).stack;

      const getRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:teamId' &&
          layer.route.methods.get,
      );
      expect(getRoute).to.not.be.undefined;
    });

    it('should register PUT /:teamId route', () => {
      const router = createTeamsRouter(container);
      const routes = (router as any).stack;

      const putRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:teamId' &&
          layer.route.methods.put,
      );
      expect(putRoute).to.not.be.undefined;
    });

    it('should register DELETE /:teamId route', () => {
      const router = createTeamsRouter(container);
      const routes = (router as any).stack;

      const deleteRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:teamId' &&
          layer.route.methods.delete,
      );
      expect(deleteRoute).to.not.be.undefined;
    });

    it('should register GET /:teamId/users route', () => {
      const router = createTeamsRouter(container);
      const routes = (router as any).stack;

      const usersRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:teamId/users' &&
          layer.route.methods.get,
      );
      expect(usersRoute).to.not.be.undefined;
    });
  });

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createTeamsRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      // POST /, GET /user/teams, GET /:teamId, PUT /:teamId, DELETE /:teamId, GET /:teamId/users = 6
      expect(routes.length).to.equal(6);
    });
  });

  describe('handler wiring', () => {
    const VALID_TEAM_ID = '550e8400-e29b-41d4-a716-446655440000';

    function findRouteHandler(router: any, path: string, method: string) {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === path && l.route.methods[method],
      );
      if (!layer) return null;
      return layer.route.stack[layer.route.stack.length - 1].handle;
    }

    function createMockReqRes() {
      const mockReq: any = {
        user: { userId: '507f1f77bcf86cd799439011', orgId: '507f1f77bcf86cd799439012' },
        body: { name: 'Team' },
        params: { teamId: VALID_TEAM_ID },
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

    it('POST / handler should call teamsController.createTeam', async () => {
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/', 'post');
      const next = sinon.stub();
      await handler({ body: { name: 'Team' } }, { status: sinon.stub().returnsThis(), json: sinon.stub() }, next);
      expect(mockTeamsController.createTeam.calledOnce).to.be.true;
    });

    it('POST / handler should call next on error', async () => {
      mockTeamsController.createTeam.rejects(new Error('Create failed'));
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/', 'post');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('GET /user/teams handler should call teamsController.getUserTeams', async () => {
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/user/teams', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockTeamsController.getUserTeams.calledOnce).to.be.true;
    });

    it('GET /:teamId handler should call teamsController.getTeam', async () => {
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/:teamId', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockTeamsController.getTeam.calledOnce).to.be.true;
    });

    it('PUT /:teamId handler should call teamsController.updateTeam', async () => {
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/:teamId', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockTeamsController.updateTeam.calledOnce).to.be.true;
    });

    it('DELETE /:teamId handler should call teamsController.deleteTeam', async () => {
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/:teamId', 'delete');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockTeamsController.deleteTeam.calledOnce).to.be.true;
    });

    it('GET /:teamId/users handler should call teamsController.getTeamUsers', async () => {
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/:teamId/users', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockTeamsController.getTeamUsers.calledOnce).to.be.true;
    });

    it('DELETE /:teamId handler should call next on error', async () => {
      mockTeamsController.deleteTeam.rejects(new Error('Delete failed'));
      const router = createTeamsRouter(container);
      const handler = findRouteHandler(router, '/:teamId', 'delete');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });
  });
});
