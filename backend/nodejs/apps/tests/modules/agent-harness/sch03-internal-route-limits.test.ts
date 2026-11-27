/**
 * CodeQL js/missing-rate-limiting on PR #6718: every route of the internal router's schedules toolset carries a per-run
 * rate limit after its attempt-token guard, reads included, so a broken or hostile worker cannot flood a store read.
 */
import { expect } from 'chai';
import { Container } from 'inversify';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_CHAT_SCHEDULES,
  AGENT_HARNESS_EXECUTION_SERVICE,
} from '../../../src/modules/agent-harness/binders/tokens';

interface RouteLayer {
  route?: { path: string; methods: Record<string, boolean>; stack: Array<{ handle: unknown }> };
}

/** express-rate-limit's middleware: the only handler on these routes that can reset a key. */
const isLimiter = (handle: unknown): boolean =>
  typeof handle === 'function' && typeof (handle as { resetKey?: unknown }).resetKey === 'function';

describe('SCH-03: the schedules toolset’s internal routes are rate-limited per run', () => {
  const container = new Container();
  container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({});
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
  container.bind(AGENT_HARNESS_CHAT_SCHEDULES).toConstantValue({});
  const routes = (createAgentHarnessInternalRouter(container).stack as RouteLayer[])
    .filter((layer) => layer.route?.path.startsWith('/runs/:runId/schedules') === true)
    .flatMap((layer) =>
      Object.keys(layer.route!.methods).map((method) => ({
        route: `${method.toUpperCase()} ${layer.route!.path}`,
        limited: layer.route!.stack.some((entry) => isLimiter(entry.handle)),
      })),
    );

  it('mounts the five routes: create, find, read, update and delete', () => {
    expect(routes.map((r) => r.route).sort()).to.deep.equal([
      'GET /runs/:runId/schedules',
      'GET /runs/:runId/schedules/:scheduleId',
      'POST /runs/:runId/schedules',
      'POST /runs/:runId/schedules/:scheduleId/delete',
      'POST /runs/:runId/schedules/:scheduleId/update',
    ]);
  });

  it('limits every one of them, the reads too', () => {
    expect(routes.filter((r) => !r.limited).map((r) => r.route)).to.deep.equal([]);
  });
});
