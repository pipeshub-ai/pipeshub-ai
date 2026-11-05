import 'reflect-metadata';
import { expect } from 'chai';
import { createProjectsRouter } from '../../../../src/modules/projects/routes/project.routes';
import { requireProjectsEnabled } from '../../../../src/modules/projects/utils/projects-feature';

describe('projects/routes/project.routes', () => {
  const authenticate = function authenticate() {};
  const container = {
    get: (id: string) => (id === 'AuthMiddleware' ? { authenticate } : {}),
  } as any;

  it('checks the Projects flag after authenticate on every route', () => {
    const router = createProjectsRouter(container);
    const routes = router.stack.filter((layer: any) => layer.route);

    expect(router.stack.length).to.equal(routes.length);
    expect(routes.length).to.be.greaterThan(0);
    for (const layer of routes as any[]) {
      const handlers: unknown[] = layer.route.stack.map((s: any) => s.handle);
      const auth = handlers.indexOf(authenticate);
      expect(auth, layer.route.path).to.be.greaterThan(-1);
      expect(handlers.indexOf(requireProjectsEnabled), layer.route.path).to.equal(auth + 1);
    }
  });
});
