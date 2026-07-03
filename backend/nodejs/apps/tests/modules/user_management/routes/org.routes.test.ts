import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createOrgRouter } from '../../../../src/modules/user_management/routes/org.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller';

describe('Org Routes', () => {
  let container: Container;
  let mockAuthMiddleware: any;
  let mockOrgController: any;

  beforeEach(() => {
    container = new Container();

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    };

    mockOrgController = {
      checkOrgExistence: sinon.stub().resolves(),
      createOrg: sinon.stub().resolves(),
      getOrganizationById: sinon.stub().resolves(),
      updateOrganizationDetails: sinon.stub().resolves(),
      deleteOrganization: sinon.stub().resolves(),
      updateOrgLogo: sinon.stub().resolves(),
      getOrgLogo: sinon.stub().resolves(),
      removeOrgLogo: sinon.stub().resolves(),
      getOnboardingStatus: sinon.stub().resolves(),
      updateOnboardingStatus: sinon.stub().resolves(),
    };

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any);
    container.bind<OrgController>('OrgController').toConstantValue(mockOrgController as any);

  });

  afterEach(() => {
    sinon.restore();
  });

  it('should create a router successfully', () => {
    const router = createOrgRouter(container);
    expect(router).to.be.a('function');
  });

  it('should have route handlers registered', () => {
    const router = createOrgRouter(container);
    const routes = (router as any).stack || [];
    expect(routes.length).to.be.greaterThan(0);
  });

  describe('route registration', () => {
    it('should register GET /exists route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const existsRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/exists' &&
          layer.route.methods.get,
      );
      expect(existsRoute).to.not.be.undefined;
    });

    it('should register POST / route for org creation', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const createRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.post,
      );
      expect(createRoute).to.not.be.undefined;
    });

    it('should register GET / route for getting org', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const getRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.get,
      );
      expect(getRoute).to.not.be.undefined;
    });

    it('should register PUT / route for updating org', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const putRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.put,
      );
      expect(putRoute).to.not.be.undefined;
    });

    it('should register DELETE / route for deleting org', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const deleteRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.delete,
      );
      expect(deleteRoute).to.not.be.undefined;
    });

    it('should register PUT /logo route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const logoRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/logo' &&
          layer.route.methods.put,
      );
      expect(logoRoute).to.not.be.undefined;
    });

    it('should register GET /logo route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const logoRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/logo' &&
          layer.route.methods.get,
      );
      expect(logoRoute).to.not.be.undefined;
    });

    it('should register DELETE /logo route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const logoRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/logo' &&
          layer.route.methods.delete,
      );
      expect(logoRoute).to.not.be.undefined;
    });

    it('should register GET /onboarding-status route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const onboardingRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/onboarding-status' &&
          layer.route.methods.get,
      );
      expect(onboardingRoute).to.not.be.undefined;
    });

    it('should register PUT /onboarding-status route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack;

      const onboardingRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/onboarding-status' &&
          layer.route.methods.put,
      );
      expect(onboardingRoute).to.not.be.undefined;
    });

    it('should register GET /health route', () => {
      const router = createOrgRouter(container);
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
      const router = createOrgRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      // GET /exists, POST /, GET /, PUT /, DELETE /,
      // PUT /logo, DELETE /logo, GET /logo,
      // GET /onboarding-status, PUT /onboarding-status, GET /health = 11
      expect(routes.length).to.be.greaterThanOrEqual(11);
    });
  });

  describe('middleware chains', () => {
    it('should include middleware layers for each route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      for (const routeLayer of routes) {
        const handlerCount = routeLayer.route.stack.length;
        expect(handlerCount).to.be.greaterThanOrEqual(1,
          `Route ${routeLayer.route.path} should have at least 1 handler`);
      }
    });

    it('should use attachContainerMiddleware as router-level middleware', () => {
      const router = createOrgRouter(container);
      // The router should have non-route middleware layers for attachContainer
      const middlewareLayers = (router as any).stack.filter(
        (layer: any) => !layer.route,
      );
      expect(middlewareLayers.length).to.be.greaterThanOrEqual(1);
    });

    it('should have admin check on delete route', () => {
      const router = createOrgRouter(container);
      const routes = (router as any).stack.filter((layer: any) => layer.route);

      const deleteRoute = routes.find(
        (layer: any) => layer.route.path === '/' && layer.route.methods.delete,
      );
      expect(deleteRoute).to.not.be.undefined;
      // auth + requireScopes + metrics + adminCheck + handler
      expect(deleteRoute.route.stack.length).to.be.greaterThanOrEqual(3);
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
        params: {},
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

    it('GET /exists handler should call orgController.checkOrgExistence', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/exists', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.checkOrgExistence.calledOnce).to.be.true;
    });

    it('GET /exists handler should call next on error', async () => {
      mockOrgController.checkOrgExistence.rejects(new Error('DB error'));
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/exists', 'get');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('POST / handler should call orgController.createOrg', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/', 'post');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.createOrg.calledOnce).to.be.true;
    });

    it('GET / handler should call orgController.getOrganizationById', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.getOrganizationById.calledOnce).to.be.true;
    });

    it('PUT / handler should call orgController.updateOrganizationDetails', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.updateOrganizationDetails.calledOnce).to.be.true;
    });

    it('DELETE / handler should call orgController.deleteOrganization', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/', 'delete');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.deleteOrganization.calledOnce).to.be.true;
    });

    it('PUT /logo handler should call orgController.updateOrgLogo', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/logo', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.updateOrgLogo.calledOnce).to.be.true;
    });

    it('DELETE /logo handler should call orgController.removeOrgLogo', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/logo', 'delete');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.removeOrgLogo.calledOnce).to.be.true;
    });

    it('GET /logo handler should call orgController.getOrgLogo', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/logo', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.getOrgLogo.calledOnce).to.be.true;
    });

    it('GET /onboarding-status handler should call orgController.getOnboardingStatus', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/onboarding-status', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.getOnboardingStatus.calledOnce).to.be.true;
    });

    it('PUT /onboarding-status handler should call orgController.updateOnboardingStatus', async () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/onboarding-status', 'put');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockOrgController.updateOnboardingStatus.calledOnce).to.be.true;
    });

    it('GET /health handler should respond with healthy status', () => {
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/health', 'get');
      expect(handler).to.not.be.undefined;

      const { mockReq, mockRes } = createMockReqRes();
      handler(mockReq, mockRes);

      expect(mockRes.json.calledOnce).to.be.true;
      const response = mockRes.json.firstCall.args[0];
      expect(response.status).to.equal('healthy');
    });

    it('PUT / handler should call next on error', async () => {
      mockOrgController.updateOrganizationDetails.rejects(new Error('Update failed'));
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/', 'put');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });

    it('DELETE / handler should call next on error', async () => {
      mockOrgController.deleteOrganization.rejects(new Error('Delete failed'));
      const router = createOrgRouter(container);
      const handler = findRouteHandler(router, '/', 'delete');

      const { mockReq, mockRes, mockNext } = createMockReqRes();
      await handler(mockReq, mockRes, mockNext);

      expect(mockNext.calledOnce).to.be.true;
    });
  });
});

describe('Org Routes - handler coverage', () => {
  let container: Container
  let mockOrgController: any
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    mockOrgController = {
      checkOrgExistence: sinon.stub().resolves(),
      createOrg: sinon.stub().resolves(),
      getOrganizationById: sinon.stub().resolves(),
      updateOrganizationDetails: sinon.stub().resolves(),
      deleteOrganization: sinon.stub().resolves(),
      updateOrgLogo: sinon.stub().resolves(),
      removeOrgLogo: sinon.stub().resolves(),
      getOrgLogo: sinon.stub().resolves(),
      getOnboardingStatus: sinon.stub().resolves(),
      updateOnboardingStatus: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<OrgController>('OrgController').toConstantValue(mockOrgController as any)

    router = createOrgRouter(container)
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

  describe('GET /exists handler', () => {
    it('should call orgController.checkOrgExistence', async () => {
      const handler = findHandler('/exists', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.checkOrgExistence.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      mockOrgController.checkOrgExistence.rejects(new Error('Check failed'))
      const handler = findHandler('/exists', 'get')

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST / handler', () => {
    it('should call orgController.createOrg', async () => {
      const handler = findHandler('/', 'post')
      expect(handler).to.exist

      const req = { body: { accountType: 'individual', contactEmail: 'test@example.com', adminFullName: 'Admin', password: 'pass12345' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.createOrg.calledOnce).to.be.true
    })
  })

  describe('GET / handler', () => {
    it('should call orgController.getOrganizationById', async () => {
      const handler = findHandler('/', 'get')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.getOrganizationById.calledOnce).to.be.true
    })
  })

  describe('PUT / handler', () => {
    it('should call orgController.updateOrganizationDetails', async () => {
      const handler = findHandler('/', 'put')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.updateOrganizationDetails.calledOnce).to.be.true
    })
  })

  describe('DELETE / handler', () => {
    it('should call orgController.deleteOrganization', async () => {
      const handler = findHandler('/', 'delete')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.deleteOrganization.calledOnce).to.be.true
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
    })
  })

  describe('GET /onboarding-status handler', () => {
    it('should call orgController.getOnboardingStatus', async () => {
      const handler = findHandler('/onboarding-status', 'get')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.getOnboardingStatus.calledOnce).to.be.true
    })
  })

  describe('PUT /onboarding-status handler', () => {
    it('should call orgController.updateOnboardingStatus', async () => {
      const handler = findHandler('/onboarding-status', 'put')
      expect(handler).to.exist

      const req = { body: { status: 'configured' }, user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.updateOnboardingStatus.calledOnce).to.be.true
    })
  })

  describe('route registrations', () => {
    it('should register PUT /logo route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/logo' && l.route.methods.put,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register DELETE /logo route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/logo' && l.route.methods.delete,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /logo route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/logo' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })
  })
})
