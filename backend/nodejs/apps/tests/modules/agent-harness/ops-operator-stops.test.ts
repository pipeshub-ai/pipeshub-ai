/**
 * ADR-OPS-01 operator stops as this build holds them: the deployment's env switch
 * (`AGENT_HARNESS_KILL_SWITCH`), and the enforcement any source of stops shares
 * (OPS-01 one model of scope, OPS-02 the sweeper's pass, OPS-03 admission's refusal,
 * OPS-06 the run's ending). A source with stored controls is a test double here.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryControlPlaneLiveness } from '../../../src/modules/agent-harness/adapters/in-memory-control-plane-liveness';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { GatewayTokenService } from '../../../src/modules/agent-harness/application/gateway-token-service';
import { OperatorStopService } from '../../../src/modules/agent-harness/application/operator-controls';
import {
  LIVENESS_GAP_MS,
  RunSweeper,
  SWEEP_INTERVAL_MS,
} from '../../../src/modules/agent-harness/application/run-sweeper';
import { OperatorStopStep } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { ActorReadmission } from '../../../src/modules/agent-harness/application/actor-readmission';
import { RunAdmissionScheduledRuns } from '../../../src/modules/agent-harness/application/scheduled-run-admission';
import { AdmissionRefusal } from '../../../src/modules/agent-harness/application/admission/refusals';
import {
  HarnessConflictError,
  HarnessOperatorControlRefusedError,
} from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  admissionStop,
  coveringControls,
  covers,
  egressStopped,
  OPERATOR_STOP_BOUND_MS,
  orgStopped,
  stoppedEngines,
  type OperatorControl,
  type OperatorScope,
} from '../../../src/modules/agent-harness/domain/operator-controls';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { runTranscript } from '../../../src/modules/agent-harness/projection/transcript';
import type { OpenRunFilter } from '../../../src/modules/agent-harness/ports/execution-store';
import {
  contractClock,
  ContractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { proofFor, testBindingKeys } from './support/binding-proof';
import { without } from './support/without';

const control = (
  scope: OperatorScope,
  orgId = 'org-1',
  overrides: Partial<OperatorControl> = {},
): OperatorControl => ({
  id: `${scope.kind}:${orgId}`,
  scope,
  orgId: scope.kind === 'instance' ? null : orgId,
  reason: 'security',
  note: null,
  setBy: 'p_admin',
  setAt: 1,
  ...overrides,
});
describe('OPS-01: one model of scope (ADR-OPS-01 D2)', () => {
  const run = {
    orgId: 'org-1',
    sessionId: 's-1',
    agentKey: 'agent-a',
    engine: { kind: 'codex', version: '0.160.0' },
  };
  const loop = { ...run, engine: null };

  it('each scope covers exactly the runs it names, and only in its own org', () => {
    expect(covers(control({ kind: 'session', sessionId: 's-1' }), run)).to.equal(true);
    expect(covers(control({ kind: 'session', sessionId: 's-1' }, 'org-2'), run)).to.equal(false);
    expect(covers(control({ kind: 'session', sessionId: 's-2' }), run)).to.equal(false);
    expect(covers(control({ kind: 'agent', agentKey: 'agent-a' }), run)).to.equal(true);
    expect(covers(control({ kind: 'agent', agentKey: 'agent-a' }, 'org-2'), run)).to.equal(false);
    expect(covers(control({ kind: 'org' }), run)).to.equal(true);
    expect(covers(control({ kind: 'org' }, 'org-2'), run)).to.equal(false);
    const codex = { kind: 'engine_version', engine: { kind: 'codex', version: '0.160.0' } } as const;
    const version = control(codex);
    expect(covers(version, run)).to.equal(true);
    expect(covers(version, { ...run, engine: { kind: 'codex', version: '0.159.0' } })).to.equal(false);
    expect(covers(version, loop)).to.equal(false);
    expect(covers(control(codex, 'org-2'), run)).to.equal(false);
    expect(covers(control({ kind: 'instance' }), loop)).to.equal(true);
    expect(covers(control({ kind: 'egress' }), run)).to.equal(true);
    expect(covers(control({ kind: 'egress' }), loop)).to.equal(false);
    expect(covers(control({ kind: 'egress' }, 'org-2'), run)).to.equal(false);
  });

  it('an engine version or egress control stored with no org still covers every org', () => {
    const everyOrg = { orgId: null };
    const version = control({ kind: 'engine_version', engine: { kind: 'codex', version: '0.160.0' } }, 'org-2', everyOrg);
    const egress = control({ kind: 'egress' }, 'org-2', everyOrg);
    expect(covers(version, run)).to.equal(true);
    expect(covers(egress, run)).to.equal(true);
    expect([...stoppedEngines([version], 'org-1')]).to.deep.equal(['codex@0.160.0']);
    expect(egressStopped([egress], 'org-1')).to.equal(true);
  });

  it('a person is told the narrowest stop', () => {
    const all = [
      control({ kind: 'instance' }),
      control({ kind: 'org' }),
      control({ kind: 'agent', agentKey: 'agent-a' }),
      control({ kind: 'session', sessionId: 's-1' }),
    ];
    expect(coveringControls(run, all).map((c) => c.scope.kind)).to.deep.equal([
      'session',
      'agent',
      'org',
      'instance',
    ]);
  });

  it('admission, the kill switch and the engine check each read the same controls', () => {
    const all = [
      control({ kind: 'agent', agentKey: 'agent-a' }),
      control({ kind: 'org' }, 'org-2'),
      control({ kind: 'engine_version', engine: { kind: 'codex', version: '0.160.0' } }),
    ];
    expect(admissionStop(all, { orgId: 'org-1', sessionId: 's-9', agentKey: 'agent-a' })?.scope.kind).to.equal('agent');
    expect(admissionStop(all, { orgId: 'org-1', sessionId: 's-9', agentKey: 'agent-b' })).to.equal(null);
    expect(orgStopped(all, 'org-2')).to.equal(true);
    expect(orgStopped(all, 'org-1')).to.equal(false);
    expect(orgStopped([control({ kind: 'instance' })], 'org-1')).to.equal(true);
    expect([...stoppedEngines(all, 'org-1')]).to.deep.equal(['codex@0.160.0']);
    expect([...stoppedEngines(all, 'org-2')]).to.deep.equal([]);
    expect(egressStopped(all, 'org-1')).to.equal(false);
  });
});


/** A source of stops beyond the env switch: what an extension keeps, as a list a test sets. */
class StoredStops extends OperatorStopService {
  stored: OperatorControl[] = [];

  protected override storedControls(): Promise<OperatorControl[]> {
    return Promise.resolve(this.stored);
  }
}

describe('OPS-02 / OPS-03: operator stops on admitted work (C11.06, J-19)', function () {
  this.timeout(20_000);
  let clock: ContractClock;
  let store: InMemoryExecutionStore;
  let h: ContractHarness;
  let engines: Map<string, { kind: string; version: string }>;

  const newService = (deploymentKillSwitch = false): StoredStops =>
    new StoredStops({
      // The store pins no engine for these runs: the test names which engine each run holds.
      runs: {
        get: (orgId, runId) => store.get(orgId, runId),
        openRuns: async (filter: OpenRunFilter, limit: number) => {
          const rest = without(filter, 'engine', 'engineOnly');
          return (await store.openRuns(rest, limit)).map(({ run }) => ({
            run,
            engine: engines.get(run.runId) ?? null,
          }));
        },
      },
      executions: h.service,
      deploymentKillSwitch,
    });

  beforeEach(() => {
    clock = contractClock();
    store = new InMemoryExecutionStore(clock);
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    engines = new Map();
  });

  const running = async (overrides: Record<string, unknown> = {}): Promise<HarnessRun> => {
    const { run } = await h.admit(overrides);
    const claimed = await h.service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: `attempt-${run.runId}`,
      leaseDurationMs: 60_000,
      workerCapabilities: CURRENT_WORKER_CAPABILITIES,
    } as never);
    return claimed.run;
  };
  const statusOf = async (run: HarnessRun) => (await h.service.get(run.orgId, run.runId))!.status;

  it('with no env switch the built-in source holds no stop, refuses nothing and cancels nothing', async () => {
    const live = await running();
    const builtIn = newService();
    expect(await builtIn.controls()).to.deep.equal([]);
    expect(await builtIn.admissionStop({ orgId: h.tenant.orgId, sessionId: live.sessionId })).to.equal(null);
    expect([...(await builtIn.stoppedEngines(h.tenant.orgId))]).to.deep.equal([]);
    expect(await builtIn.egressStopped(h.tenant.orgId)).to.equal(false);
    expect(await builtIn.enforceAll()).to.equal(0);
    expect(await statusOf(live)).to.equal('running');
  });

  it("C11.06: the deployment's AGENT_HARNESS_KILL_SWITCH is the instance stop, enforced on every org's open runs", async () => {
    const live = await running();
    const deployed = newService(true);
    const [instance] = await deployed.controls();
    expect(instance).to.deep.include({ id: 'instance', orgId: null, setBy: 'deployment' });
    expect(orgStopped(await deployed.controls(), h.tenant.orgId)).to.equal(true);
    expect(await deployed.enforceAll()).to.equal(1);
    expect(await statusOf(live)).to.be.oneOf(['cancelling', 'cancelled']);
    const stops = (await store.journal.readRun(live.orgId, live.runId, 0, 500)).filter(
      (event) => event.type === 'run.cancel_requested',
    );
    expect(stops.map((event) => (event.data as Record<string, unknown>).stop)).to.deep.equal([
      { scope: 'instance', reason: 'incident' },
    ]);
  });

  it('OPS-02: the sweeper tick and a leader handover stay inside the 60 s bound', () => {
    expect(OPERATOR_STOP_BOUND_MS).to.equal(60_000);
    expect(SWEEP_INTERVAL_MS + LIVENESS_GAP_MS).to.be.at.most(OPERATOR_STOP_BOUND_MS);
  });

  it('OPS-02: a run admitted in the race with a stop is cancelled at the next sweeper tick and its gateway token refused', async () => {
    const raced = await running();
    const gateway = new GatewayTokenService({
      tokens: new GatewayTokenAuthority({
        signingKeys: new InMemoryConfigDocumentStore(),
        publishedKeys: new InMemoryConfigDocumentStore(),
        clock,
      }),
      runs: h.service,
      clock,
      bindingKeys: testBindingKeys,
    });
    const issued = await gateway.forAttempt(
      raced.orgId,
      raced.runId,
      raced.attemptId!,
      proofFor(raced.orgId, raced.runId, raced.attemptId!),
    );
    expect(await statusOf(raced)).to.equal('running');
    const stops = newService();
    stops.stored = [control({ kind: 'org' }, h.tenant.orgId)];

    const sweeper = new RunSweeper({
      executions: { sweep: sinon.stub().resolves(0) },
      lease: { tryAcquire: sinon.stub().resolves(true), release: sinon.stub().resolves() },
      liveness: new InMemoryControlPlaneLiveness(),
      logger: { debug() {}, info() {}, warn() {}, error() {} },
      holderId: 'holder-a',
      operatorControls: stops,
    });
    await sweeper.tick();

    expect(await statusOf(raced)).to.be.oneOf(['cancelling', 'cancelled']);
    const refused = await gateway.modelGrant(issued.token, raced.runId).catch((e: unknown) => e);
    expect((refused as HarnessConflictError).code).to.equal('RUN_NOT_LEASED');
  });

  it('OPS-03: admission refuses a turn under a stop with OPERATOR_STOPPED naming the scope, never the note', async () => {
    const stops = newService();
    stops.stored = [control({ kind: 'agent', agentKey: 'agent-a' }, h.tenant.orgId, { note: 'cve' })];
    const step = new OperatorStopStep(stops);
    const refusal = await step
      .run({ request: { orgId: h.tenant.orgId, agentKey: 'agent-a' } } as never)
      .catch((e: unknown) => e);
    expect(refusal).to.be.instanceOf(AdmissionRefusal);
    const error = (refusal as AdmissionRefusal).error as HarnessOperatorControlRefusedError;
    expect(error.code).to.equal('OPERATOR_STOPPED');
    expect(error.details).to.deep.equal({ scope: 'agent', reason: 'security' });
    expect(JSON.stringify(error)).to.not.include('cve');
    await step.run({ request: { orgId: h.tenant.orgId, agentKey: 'agent-b' } } as never);
    // The built-in source refuses nothing.
    await new OperatorStopStep(newService()).run({ request: { orgId: h.tenant.orgId, agentKey: 'agent-a' } } as never);
  });

  it('OPS-15: a control whose effect this build does not apply leaves its runs as they are', async () => {
    const live = await running();
    engines.set(live.runId, { kind: 'codex', version: '0.160.0' });
    const stops = newService();
    stops.stored = [
      control({ kind: 'engine_version', engine: { kind: 'codex', version: '0.160.0' } }, h.tenant.orgId, {
        id: 'retired',
        effect: 'retire',
      }),
    ];
    expect(await stops.enforceAll()).to.equal(0);
    expect(await statusOf(live)).to.equal('running');
    // Admission still refuses the version: a new run never starts on it.
    expect([...(await stops.stoppedEngines(h.tenant.orgId))]).to.deep.equal(['codex@0.160.0']);
  });

  it('OPS-03: every entry path refuses under a stop through the same check: a fresh turn, a drained next run and a schedule firing', async () => {
    const stops = newService();
    stops.stored = [control({ kind: 'agent', agentKey: 'agent-ops' }, h.tenant.orgId, { reason: 'cost' })];
    h.setPolicySteps([new OperatorStopStep(stops)]);
    const agentRun = { agentKey: 'agent-ops', authorization: 'Bearer user' };
    const queued = {
      orgId: h.tenant.orgId,
      actorId: h.tenant.actorId,
      agentKey: 'agent-ops',
      sessionId: null,
      idempotencyKey: 'ops-03-queued',
      absoluteDeadlineAt: 70_000,
      via: 'session',
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    } as never;

    const fresh = await h.submit(agentRun as never).catch((e: unknown) => e);
    expect((fresh as HarnessOperatorControlRefusedError).code).to.equal('OPERATOR_STOPPED');

    const drained = await h.readmit(queued).catch((e: unknown) => e);
    expect((drained as HarnessOperatorControlRefusedError).code).to.equal('OPERATOR_STOPPED');
    const readmission = new ActorReadmission(
      { authorize: async () => ({ authorization: 'Bearer user' }) } as never,
      () => ({ readmit: (command) => h.readmit(command) }),
    );
    expect(await readmission.readmit(queued)).to.deep.equal({ refused: 'operator_stopped' });

    const firing = await new RunAdmissionScheduledRuns({ submit: (request) => h.submit(request) }).admit({
      orgId: h.tenant.orgId,
      actorId: h.tenant.actorId,
      agentKey: 'agent-ops',
      idempotencyKey: 'ops-03-firing',
      deadlineMs: 9_000,
      input: { query: 'scheduled', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      authorization: 'Bearer user',
      trigger: { kind: 'schedule', scheduleId: 'sch-1', firingId: 'fire-1' } as never,
    });
    expect(firing).to.deep.equal({ kind: 'refused', reason: 'operator_stopped' });
  });

  it('OPS-06: a run an operator stopped ends as the run error operator_stopped, in the stream and the saved conversation', async () => {
    const queued = (await h.admit()).run;
    const stops = newService();
    await stops.enforce(control({ kind: 'session', sessionId: queued.sessionId }, h.tenant.orgId, { reason: 'cost' }));
    const events = await store.journal.readRun(queued.orgId, queued.runId, 0, 500);
    expect(foldTimeline(events, queued.runId).stoppedBy).to.deep.equal({ scope: 'session', reason: 'cost' });
    expect(runTranscript(events, queued.runId)).to.deep.include({ kind: 'failure', code: 'operator_stopped' });
  });
});
