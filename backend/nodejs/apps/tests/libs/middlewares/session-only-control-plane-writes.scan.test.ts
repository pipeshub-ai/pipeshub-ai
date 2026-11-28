import 'reflect-metadata';
import { expect } from 'chai';
import { Router } from 'express';
import { Container } from 'inversify';
import { routeChains } from '../../helpers/session-only-routes';
import { requireSessionAuth } from '../../../src/libs/middlewares/require-session-auth.middleware';
import { OAuthScopeNames } from '../../../src/libs/enums/oauth-scopes.enum';
import {
  CONTROL_PLANE_ROUTES,
  registerCpRoutes,
} from '../../../src/modules/control-plane';
import { createAgentHarnessAdminRouter } from '../../../src/modules/agent-harness/transport/admin-routes';

const guarded = (handlers: unknown[]): boolean =>
  handlers.includes(requireSessionAuth);

// After authentication and before the config:write check, so a token is told it needs a
// session rather than that it lacks a scope it can no longer be granted.
const guardedInOrder = (handlers: unknown[], authenticate: unknown): boolean => {
  const guard = handlers.indexOf(requireSessionAuth);
  const scopeCheck = handlers.findIndex((h) =>
    (h as { requiredScopes?: readonly string[] }).requiredScopes?.includes(
      OAuthScopeNames.CONFIG_WRITE,
    ),
  );
  return (
    handlers.indexOf(authenticate) === guard - 1 &&
    guard > 0 &&
    guard < scopeCheck
  );
};

describe('configuration writes of the control plane and the agent harness take an interactive session', () => {
  it('every control-plane route that asks config:write', () => {
    const router = registerCpRoutes(
      Router(),
      new Container(),
      CONTROL_PLANE_ROUTES,
    );
    const chains = new Map(
      routeChains(router).map((route) => [
        `${route.method} ${route.path}`.toLowerCase(),
        route.handlers,
      ]),
    );
    const writes = CONTROL_PLANE_ROUTES.filter((spec) =>
      spec.scopes.includes(OAuthScopeNames.CONFIG_WRITE),
    );
    expect(writes.map((spec) => spec.path)).to.include.members([
      '/bindings',
      '/bindings/:bindingId/actions/rotate',
    ]);
    for (const spec of CONTROL_PLANE_ROUTES) {
      const handlers =
        chains.get(`${spec.method} ${spec.path}`.toLowerCase()) ?? [];
      if (writes.includes(spec)) {
        expect(
          guardedInOrder(handlers, handlers[0]),
          `${spec.method} ${spec.path}`,
        ).to.equal(true);
      } else {
        expect(guarded(handlers), `${spec.method} ${spec.path}`).to.equal(
          false,
        );
      }
    }
  });

  it("the organization's coding engine", () => {
    const authenticate = () => undefined;
    const container = new Container();
    container.bind('AuthMiddleware').toConstantValue({ authenticate });
    const route = routeChains(createAgentHarnessAdminRouter(container)).find(
      (r) => r.method === 'PUT' && r.path === '/coding-engine',
    );
    expect(guardedInOrder(route?.handlers ?? [], authenticate)).to.equal(true);
  });
});
