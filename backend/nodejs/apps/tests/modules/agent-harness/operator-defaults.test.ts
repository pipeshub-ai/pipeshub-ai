/**
 * What this build does without an org's admin controls: the operator stops are the deployment's env switch alone,
 * the rollout reads no stop beyond it, and the admin router serves no runs list, controls, policy webhook, audit
 * search or download audit. People still cancel their own runs (the run routes) and every turn runs as before.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import type { Router } from 'express';
import type { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { PlatformHarnessRolloutSettings } from '../../../src/modules/agent-harness/adapters/configuration/platform-rollout-settings';
import { OperatorStopService } from '../../../src/modules/agent-harness/application/operator-controls';
import { bindOperatorControls } from '../../../src/modules/agent-harness/binders/operator-controls';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import {
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_OPERATOR_CONTROLS,
} from '../../../src/modules/agent-harness/binders/tokens';
import { harnessOrgStops } from '../../../src/modules/agent-harness/composition';
import { noopHarnessEdition } from '../../../src/modules/agent-harness/edition';
import type { OperatorStops } from '../../../src/modules/agent-harness/ports/operator-stops';
import { createAgentHarnessAdminRouter } from '../../../src/modules/agent-harness/transport/admin-routes';
import { READY } from './support/rollout';

const SETTINGS = {
  get: async () => null,
  compareAndSet: async () => true,
} as never;

const withKillSwitch = (on: boolean, run: () => Promise<void>) => async () => {
  const before = process.env.AGENT_HARNESS_KILL_SWITCH;
  if (on) process.env.AGENT_HARNESS_KILL_SWITCH = 'true';
  else delete process.env.AGENT_HARNESS_KILL_SWITCH;
  try {
    await run();
  } finally {
    if (before === undefined) delete process.env.AGENT_HARNESS_KILL_SWITCH;
    else process.env.AGENT_HARNESS_KILL_SWITCH = before;
  }
};

const bound = (): OperatorStops => {
  const container = new Container();
  container.bind(AGENT_HARNESS_EXECUTION_STORE).toConstantValue({ openRuns: async () => [], get: async () => null });
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({ control: async () => ({}) });
  bindOperatorControls({ container, logger: undefined } as unknown as HarnessBindingContext);
  return container.get<OperatorStops>(AGENT_HARNESS_OPERATOR_CONTROLS);
};

describe('operator stops without an admin control', () => {
  it('the built-in binding holds no stop, so nothing is refused or cancelled', withKillSwitch(false, async () => {
    const stops = bound();
    expect(stops).to.be.instanceOf(OperatorStopService);
    expect(await stops.controls()).to.deep.equal([]);
    expect(await stops.admissionStop({ orgId: 'org-1', sessionId: 's-1', agentKey: 'a' })).to.equal(null);
    expect(await stops.enforceAll()).to.equal(0);
  }));

  it("AGENT_HARNESS_KILL_SWITCH is the deployment's instance stop", withKillSwitch(true, async () => {
    expect((await bound().controls()).map((control) => control.scope.kind)).to.deep.equal(['instance']);
  }));

  it("a binding made before the built-in one is kept (an extension's source of stops)", () => {
    const container = new Container();
    const own = { controls: async () => [] } as unknown as OperatorStops;
    container.bind(AGENT_HARNESS_OPERATOR_CONTROLS).toConstantValue(own);
    bindOperatorControls({ container, logger: undefined } as unknown as HarnessBindingContext);
    expect(container.get(AGENT_HARNESS_OPERATOR_CONTROLS)).to.equal(own);
  });
});

describe('the rollout without an admin control', () => {
  it('reads no org stop: only the env switch turns the harness off', async () => {
    expect(noopHarnessEdition.rolloutStops).to.equal(null);
    expect(harnessOrgStops(noopHarnessEdition, SETTINGS)).to.deep.equal({});
    const settings = new PlatformHarnessRolloutSettings(SETTINGS, READY);
    expect((await settings.read('org-1')).killSwitch).to.equal(false);
    const killed = new PlatformHarnessRolloutSettings(SETTINGS, { ...READY, AGENT_HARNESS_KILL_SWITCH: 'true' });
    expect((await killed.read('org-1')).killSwitch).to.equal(true);
  });

  it("an extension's org stops reach the rollout; one that cannot be read counts as on", async () => {
    const stopped = harnessOrgStops({ rolloutStops: () => async (orgId) => orgId === 'org-2' }, SETTINGS);
    const settings = new PlatformHarnessRolloutSettings(SETTINGS, READY, stopped.orgStops ?? null);
    expect((await settings.read('org-1')).killSwitch).to.equal(false);
    expect((await settings.read('org-2')).killSwitch).to.equal(true);
    const unreadable = new PlatformHarnessRolloutSettings(SETTINGS, READY, async () => {
      throw new Error('down');
    });
    expect((await unreadable.read('org-1')).killSwitch).to.equal(true);
  });
});

describe('the admin router without admin controls', () => {
  const paths = (router: Router): string[] =>
    (router.stack as Array<{ route?: { path: string } }>).flatMap((layer) =>
      layer.route !== undefined ? [layer.route.path] : [],
    );

  it('serves the dependencies view and the org coding engine only: no runs list, run cancel, controls, policy webhook, audit search or download audit', () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({ authenticate: () => undefined } as never);
    expect(paths(createAgentHarnessAdminRouter(container))).to.deep.equal(['/dependencies', '/coding-engine']);
  });
});
