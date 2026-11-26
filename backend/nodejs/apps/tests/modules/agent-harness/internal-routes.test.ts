import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';

const routeAuth = async (
  layer: any,
  authorization: string | undefined,
): Promise<unknown> => {
  const req = {
    headers: authorization === undefined ? {} : { authorization },
    params: { runId: 'run-1' },
  };
  return new Promise((resolve) => {
    layer.route.stack[0].handle(req, {}, (error?: unknown) =>
      resolve(error ?? null),
    );
  });
};

describe('agent harness internal routes', () => {
  it('A-8: claim and the grant auditor (P9) take the worker credential, metrics the scraper\'s (SEC-C-10); every later route takes the attempt token', async () => {
    const calls: string[] = [];
    const authority = {
      verifyWorker: async (credential: string | undefined) => {
        calls.push(`worker:${String(credential)}`);
      },
      verifyRole: async (roles: string[], credential: string | undefined) => {
        calls.push(`${roles.join('+')}:${String(credential)}`);
        return roles[0];
      },
      verifyAttemptToken: async (token: string | undefined, runId: string) => {
        calls.push(`attempt:${String(token)}:${runId}`);
        return { orgId: 'org-1', runId };
      },
    };
    const container = new Container();
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});

    const router = createAgentHarnessInternalRouter(container);
    const routes = router.stack.filter((layer: any) => layer.route);
    expect(routes.map((layer: any) => layer.route.path)).to.deep.equal([
      '/metrics',
      '/runs/:runId/audit',
      '/runs/claim',
      '/runs/:runId/claim',
      '/runs/:runId/renew',
      '/runs/:runId/release',
      '/runs/:runId/commit',
      '/runs/:runId/events',
      '/runs/:runId/followups',
    ]);
    for (const layer of routes) {
      expect(await routeAuth(layer, 'Bearer secret')).to.equal(null);
    }
    expect(calls).to.deep.equal([
      'scrape:secret',
      'worker:secret',
      'worker:secret',
      'worker:secret',
      'attempt:secret:run-1',
      'attempt:secret:run-1',
      'attempt:secret:run-1',
      'attempt:secret:run-1',
      'attempt:secret:run-1',
    ]);
  });

  it('passes only a well-formed bearer value to the authority', async () => {
    const seen: Array<string | undefined> = [];
    const container = new Container();
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
      verifyWorker: async (credential: string | undefined) => {
        seen.push(credential);
      },
    });
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
    const claim = createAgentHarnessInternalRouter(container).stack.find(
      (layer: any) => layer.route?.path === '/runs/:runId/claim',
    );
    for (const header of [undefined, 'Basic abc', 'Bearer', 'Bearer a b']) {
      await routeAuth(claim, header);
    }
    await routeAuth(claim, 'bearer  phw_x ');
    expect(seen).to.deep.equal([
      undefined,
      undefined,
      undefined,
      undefined,
      'phw_x',
    ]);
  });
});
