import 'reflect-metadata';
import { expect } from 'chai';
import type { Container } from 'inversify';
import { createAgentHarnessInternalRouter } from '../../src/modules/agent-harness/transport/internal-routes';
import {
  HarnessRouteGuard,
  routeGuardOf,
  routeRolesOf,
} from '../../src/modules/agent-harness/transport/internal-auth';
import type { HarnessCredentialRole } from '../../src/modules/agent-harness/domain/credential-roles';

/**
 * C-26 (H6, OI-N28): every worker-only route declares how it authenticates.
 *
 * The internal router takes no user JWT. A route there is safe only if its first
 * handler is the worker-credential guard, the attempt-token guard, or a handler
 * declared self-authenticating (its service verifies the run's or the request's
 * gateway token). A new route with none of these fails here; a new
 * self-authenticating one must be added to the list below, where a reviewer sees it,
 * and needs its own 401 case (budget-ledger.http, gateway-token.http).
 */

// As in C-10: real routers, every dependency a callable that returns itself.
const anything: unknown = new Proxy(function stub() {}, {
  get: (_target, prop) => (prop === 'then' ? undefined : anything),
  apply: () => anything,
  construct: () => anything as object,
});
const container = {
  get: () => anything,
  isBound: () => true,
  getAll: () => [],
} as unknown as Container;

interface RouteLayer {
  route?: {
    path: string;
    methods: Record<string, boolean>;
    stack: Array<{ handle: unknown }>;
  };
}

const routes = (): Array<{
  route: string;
  guard: HarnessRouteGuard | undefined;
  roles: readonly HarnessCredentialRole[] | undefined;
}> =>
  (createAgentHarnessInternalRouter(container).stack as RouteLayer[])
    .filter((layer) => layer.route !== undefined)
    .flatMap((layer) =>
      Object.keys(layer.route!.methods).map((method) => ({
        route: `${method.toUpperCase()} ${layer.route!.path}`,
        guard: routeGuardOf(layer.route!.stack[0]?.handle),
        roles: routeRolesOf(layer.route!.stack[0]?.handle),
      })),
    );

/**
 * SEC-C-10 (ADR-CRED-01): which machine role each machine-credential route admits. The scraper reads metrics and
 * nothing else; the egress proxy mints gateway tokens; only the worker claims or reads a
 * run's state. A new route, or a role added to one, changes this table where a reviewer sees it.
 */
const ROUTE_ROLES: Record<string, readonly HarnessCredentialRole[]> = {
  'GET /metrics': ['scrape'],
  'POST /runs/:runId/gateway-token': ['egress-proxy'],
  'GET /control-plane/orgs/:orgId/live': ['worker'],
  'GET /runs/:runId/audit': ['worker'],
  'POST /runs/claim': ['worker'],
  'POST /runs/:runId/claim': ['worker'],
};

const SELF_AUTHENTICATED: Record<string, HarnessRouteGuard> = {
  'GET /runs/:runId/budget': 'run-token',
  'POST /runs/:runId/budget/reservations': 'run-token',
  'POST /runs/:runId/budget/reservations/:key/commit': 'run-token',
  'POST /runs/:runId/budget/reservations/:key/reconcile': 'run-token',
  'POST /runs/:runId/budget/reservations/:key/release': 'run-token',
  'GET /runs/:runId/model-grant': 'run-token',
  'POST /runs/:runId/refusals': 'run-token',
  'POST /runs/:runId/egress': 'run-token',
  'POST /runs/:runId/egress-volume': 'run-token',
  'POST /runs/:runId/model-fallback': 'run-token',
  'POST /runs/:runId/secret-hidden': 'run-token',
};

describe('C-26: every agent-harness internal route declares its guard', () => {
  it('finds the routes (every optional part of the router built)', () => {
    const found = routes().map((r) => r.route);
    // 28 since S1R removed stage 1's six request-task budget routes.
    expect(found.length).to.be.greaterThan(25);
    expect(found).to.include('POST /runs/:runId/claim');
    expect(found).to.include('GET /runs/:runId/model-grant');
    expect(found).to.include('POST /runs/:runId/workspace-key');
  });

  it('puts a declared guard first on every route', () => {
    const unguarded = routes()
      .filter((r) => r.guard === undefined)
      .map((r) => r.route);
    expect(unguarded, 'add requireWorkerCredential, requireAttemptToken or selfAuthenticated').to.deep.equal([]);
  });

  it('lists every self-authenticating route here, with the token it verifies', () => {
    const declared = Object.fromEntries(
      routes()
        .filter((r) => r.guard === 'run-token')
        .map((r) => [r.route, r.guard]),
    );
    expect(declared).to.deep.equal(SELF_AUTHENTICATED);
  });

  it('SEC-C-10: admits each machine role only on the routes that role calls', () => {
    const admitted = Object.fromEntries(
      routes()
        .filter((r) => r.guard === 'worker-credential')
        .map((r) => [r.route, r.roles]),
    );
    expect(admitted).to.deep.equal(ROUTE_ROLES);
  });

  it('SEC-C-10: no route a run is claimed or read through admits the scraper, the proxy or the broker', () => {
    const outsiders: HarnessCredentialRole[] = ['scrape', 'egress-proxy', 'sandbox-broker'];
    const leaks = routes()
      .filter((r) => r.guard === 'worker-credential' && r.route !== 'GET /metrics')
      .filter((r) => r.route.includes('claim') || r.route.includes('live') || r.route.includes('audit'))
      .filter((r) => (r.roles ?? []).some((role) => outsiders.includes(role)))
      .map((r) => r.route);
    expect(leaks).to.deep.equal([]);
  });

  it("S1R: stage 1's request-task budget routes are gone", () => {
    expect(routes().filter((r) => r.route.includes('/requests/'))).to.deep.equal([]);
  });

  it('takes the worker credential only where a machine with no run token calls', () => {
    const worker = routes()
      .filter((r) => r.guard === 'worker-credential')
      .map((r) => r.route)
      .sort();
    expect(worker).to.deep.equal(
      [
        'GET /control-plane/orgs/:orgId/live',
        // C9.04: the deployment's scraper reads the internal-only services' metrics (R11-MX-6).
        'GET /metrics',
        'GET /runs/:runId/audit',
        'POST /runs/claim',
        'POST /runs/:runId/claim',
        'POST /runs/:runId/gateway-token',
      ].sort(),
    );
  });
});
