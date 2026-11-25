/**
 * LAT1: the server routes every chat turn through one `RoutingDecider`
 * (RT-1..RT-6), and an engine run goes straight to a worker that can run it
 * (NT-10).
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import {
  RunAdmissionService,
  SubmitDurableRun,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import { HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { legacyFor } from '../../../src/modules/agent-harness/domain/rollout';
import { InlineClaimTarget } from '../../../src/modules/agent-harness/ports/inline-claim';
import {
  RoutingDecider,
  RoutingSubject,
} from '../../../src/modules/agent-harness/ports/routing-decider';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { routingDeciderContract, SESSION } from './support/routing-decider-contract';
import { rolloutRoutingFromWorld } from './support/routing-vectors';
import { FakeEngineCeilings } from './support/org-policy-fakes';

routingDeciderContract('today’s rollout and the conversation’s placement (RolloutRoutingDecider)', rolloutRoutingFromWorld);

const runsIn = (store: InMemoryExecutionStore): number =>
  (store as unknown as { runs: Map<string, unknown> }).runs.size;

const turn = (overrides: Partial<SubmitDurableRun> = {}): SubmitDurableRun => ({
  orgId: 'org-1',
  actorId: 'user-1',
  idempotencyKey: `k-${Math.random()}`,
  firstSubmission: true,
  deadlineMs: 60_000,
  input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
  ...overrides,
});

describe('LAT1 RT-6: admission asks only the RoutingDecider', () => {
  const service = (routing: RoutingDecider, store = new InMemoryExecutionStore({ now: () => 1_000 })) => ({
    store,
    admissions: new RunAdmissionService({
      store,
      // A rollout that would admit: the decider's answer must be the one that counts.
      rollout: fixedRollout(),
      routing,
      clock: { now: () => 1_000 },
    }),
  });

  it('a legacy decision writes nothing and names its reason', async () => {
    const seen: RoutingSubject[] = [];
    const { admissions, store } = service({
      decide: async (subject) => {
        seen.push(subject);
        return { routing: legacyFor('project_scope'), scope: {} };
      },
    });
    const outcome = await admissions.submit(turn({ projectId: 'p-1' }));
    expect(outcome.route).to.equal('legacy');
    if (outcome.route === 'legacy') expect(outcome.rollout.reason).to.equal('project_scope');
    expect(runsIn(store)).to.equal(0);
    expect(seen).to.deep.equal([
      { orgId: 'org-1', actorId: 'user-1', projectId: 'p-1', model: { chatMode: 'internal_search' } },
    ]);
  });

  it('a durable decision admits', async () => {
    const { admissions } = service({
      decide: async () => ({ routing: { ...HARNESS_ENABLED, harnessMajor: 2 }, scope: {} }),
    });
    const outcome = await admissions.submit(turn());
    expect(outcome.route).to.equal('durable');
  });

  it('a decider that cannot decide refuses as unavailable (nothing written)', async () => {
    const { admissions, store } = service({
      decide: async () => {
        throw new Error('kv down');
      },
    });
    let refused: unknown;
    await admissions.submit(turn()).catch((error: unknown) => {
      refused = error;
    });
    expect(refused).to.be.instanceOf(HarnessUnavailableError);
    expect(runsIn(store)).to.equal(0);
  });

  it('the subject carries the session, agent and schedule, never the body’s ids', async () => {
    const seen: RoutingSubject[] = [];
    const { admissions } = service({
      decide: async (subject) => {
        seen.push(subject);
        return { routing: legacyFor('no_live_worker'), scope: {} };
      },
    });
    await admissions.submit(
      turn({
        agentKey: 'agent-1',
        sessionId: SESSION,
        trigger: { scheduleId: 's-1', firingId: 'f-1', slotKey: 'slot' },
        firstSubmission: false,
      }),
    );
    expect(seen[0]).to.deep.include({ agentKey: 'agent-1', sessionId: SESSION, scheduled: true });
  });
});

describe('LAT1 NT-10: an engine run is admitted for a worker that can run it', () => {
  const ENGINE_AGENT = { engines: ['opencode'], limits: { maxSteps: 25 } };

  const admit = async (engine: boolean) => {
    const store = new InMemoryExecutionStore({ now: () => 1_000 });
    const settings = new FakeEngineCeilings();
    await settings.write('org-1', { limits: { maxSteps: 30, maxUsd: 2 } }, 'p_admin');
    const hints: InlineClaimTarget[] = [];
    const outcome = await new RunAdmissionService({
      store,
      rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      clock: { now: () => 1_000 },
      agentAccess: grantingAccess((agentKey) =>
        agentDefinition({ agentKey, ...(engine ? { engineProfile: ENGINE_AGENT } : {}) }),
      ),
      inline: { hint: async (target) => void hints.push(target) },
      engineProfiles: settings,
      modelAccess: testModelAccess(),
    }).submit(
      turn({
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      }),
    );
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    return { run: outcome.admission.run, hints, dispatches: store.dispatches };
  };

  it('an engine agent’s turn runs on the loop (CTL-6): inline and hinted; only its code_task child is dispatched', async () => {
    const { run, hints, dispatches } = await admit(true);
    expect(run.profile).to.equal('inline');
    expect(run.requires?.features).to.include('code_task_tool');
    expect(hints).to.deep.equal([{ orgId: 'org-1', runId: run.runId }]);
    expect(dispatches).to.deep.equal([]);
  });

  it('a loop agent’s run stays inline, hinted to the process that serves it', async () => {
    const { run, hints, dispatches } = await admit(false);
    expect(run.profile).to.equal('inline');
    expect(hints).to.deep.equal([{ orgId: 'org-1', runId: run.runId }]);
    expect(dispatches).to.deep.equal([]);
  });
});
