import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_AUDIT_ANCHORING,
  AGENT_HARNESS_CAPABILITIES,
  AGENT_HARNESS_CHILD_RUNS,
  AGENT_HARNESS_ERASURE_WORKER,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_MODEL_ACCESS,
  AGENT_HARNESS_PUBLICATIONS,
  AGENT_HARNESS_RETENTION_SWEEPER,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_ENGINE_PROFILES,
  AGENT_HARNESS_RUN_SWEEPER,
  AGENT_HARNESS_SCHEDULE_FIRER,
  AGENT_HARNESS_SCHEDULES,
  AgentHarnessDependencies,
  bindAgentHarness,
  createActiveRunMiddleware,
  createChatTurnMiddleware,
  stopAgentHarnessBackgroundServices,
} from '../../../src/modules/agent-harness/composition';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import { RetentionSweeper } from '../../../src/modules/agent-harness/application/retention-sweeper';
import { SessionErasureWorker } from '../../../src/modules/agent-harness/application/session-erasure';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { KnowledgePublicationGateway } from '../../../src/modules/agent-harness/application/knowledge-publications';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { PinnedEngineProfile } from '../../../src/modules/agent-harness/domain/engine-profiles';
import {
  PRINCIPAL_DEACTIVATION,
  PrincipalDeactivation,
} from '../../../src/modules/agent-harness/application/principal-deactivation';
import { shareUserDeactivation } from '../../../src/app';

const dependencies = (
  extra: Partial<AgentHarnessDependencies> = {},
): AgentHarnessDependencies =>
  ({
    sessions: {},
    conversations: {},
    history: {},
    agentAccess: {},
    rollout: {},
    authority: {},
    keyEncryptionKey: {},
    ...extra,
  }) as unknown as AgentHarnessDependencies;

type EngineAuthority = (orgId: string) => Promise<PinnedEngineProfile | null>;
const engineAuthorityOf = (container: Container): EngineAuthority =>
  (
    container.get<ChildRunService>(AGENT_HARNESS_CHILD_RUNS) as unknown as {
      engineAuthority: EngineAuthority;
    }
  ).engineAuthority;

describe('agent harness composition (bindAgentHarness)', () => {
  afterEach(() => sinon.restore());

  it('binds the core services and leaves optional features unbound when their dependencies are absent', () => {
    const container = new Container();
    bindAgentHarness(container, dependencies());
    expect(container.get(AGENT_HARNESS_EXECUTION_SERVICE)).to.be.instanceOf(ExecutionService);
    expect(container.get(AGENT_HARNESS_CHILD_RUNS)).to.be.instanceOf(ChildRunService);
    expect(container.isBound(AGENT_HARNESS_RUN_ADMISSION_SERVICE)).to.equal(true);
    for (const key of [
      AGENT_HARNESS_SCHEDULES,
      AGENT_HARNESS_SCHEDULE_FIRER,
      AGENT_HARNESS_PUBLICATIONS,
      AGENT_HARNESS_MODEL_ACCESS,
      AGENT_HARNESS_ERASURE_WORKER,
      AGENT_HARNESS_RETENTION_SWEEPER,
    ]) {
      expect(container.isBound(key), key).to.equal(false);
    }
  });

  it('DEACT-11 (SEC-AZ-08): binds what a deactivation stops, and the user and knowledge-base modules borrow it', () => {
    const container = new Container();
    bindAgentHarness(container, dependencies());
    expect(container.get(PRINCIPAL_DEACTIVATION)).to.be.instanceOf(PrincipalDeactivation);
    const users = new Container();
    shareUserDeactivation(container, users);
    expect(users.get(PRINCIPAL_DEACTIVATION)).to.equal(container.get(PRINCIPAL_DEACTIVATION));
    // Without the harness there is nothing to borrow: the routes stop tokens only.
    const bare = new Container();
    shareUserDeactivation(new Container(), bare);
    expect(bare.isBound(PRINCIPAL_DEACTIVATION)).to.equal(false);
  });

  it('keeps a binding made before it (a test double or another module) instead of replacing it', () => {
    const container = new Container();
    const childRuns = { double: true };
    const publications = { double: true };
    container.bind(AGENT_HARNESS_CHILD_RUNS).toConstantValue(childRuns);
    container.bind(AGENT_HARNESS_PUBLICATIONS).toConstantValue(publications);
    bindAgentHarness(
      container,
      dependencies({ publications: { setting: {}, authority: {} } as never }),
    );
    expect(container.get(AGENT_HARNESS_CHILD_RUNS)).to.equal(childRuns);
    expect(container.get(AGENT_HARNESS_PUBLICATIONS)).to.equal(publications);
  });

  it('binds publications, schedules, model access with the given offering, and erasure when their dependencies are given', () => {
    const container = new Container();
    bindAgentHarness(
      container,
      dependencies({
        publications: { setting: {}, authority: {} } as never,
        schedules: { actors: {}, setting: {}, store: {} } as never,
        modelAccess: {
          models: {},
          offering: 'managed',
        } as never,
        erasure: {
          conversations: {},
          artifacts: {},
          liveBus: null,
          retention: {},
          agentErasures: {},
          agentDeletions: {},
        } as never,
      }),
    );
    expect(container.get(AGENT_HARNESS_PUBLICATIONS)).to.be.instanceOf(KnowledgePublicationGateway);
    expect(container.get(AGENT_HARNESS_SCHEDULE_FIRER)).to.be.instanceOf(ScheduleFirer);
    // No shared budgets bound: a schedule's budget per run is held to the release ceiling only.
    expect(
      (container.get(AGENT_HARNESS_SCHEDULES) as unknown as { budgets: unknown }).budgets,
    ).to.equal(null);
    const access = container.get<ModelAccessService>(AGENT_HARNESS_MODEL_ACCESS);
    expect(access).to.be.instanceOf(ModelAccessService);
    expect(access.offering).to.equal('managed');
    expect(container.get(AGENT_HARNESS_ERASURE_WORKER)).to.be.instanceOf(SessionErasureWorker);
    expect(container.get(AGENT_HARNESS_RETENTION_SWEEPER)).to.be.instanceOf(RetentionSweeper);
  });

  describe("an engine child's profile (E1)", () => {
    it('is the release default with no org ceiling: none is bound by default', async () => {
      const container = new Container();
      bindAgentHarness(container, dependencies());
      expect(await engineAuthorityOf(container)('org-1')).to.deep.equal(
        RELEASE_ENGINE_PROFILES[0],
      );
    });

    it("is the release default under the org's ceiling an extension binds", async () => {
      const container = new Container();
      const read = sinon.stub().resolves(null);
      container.bind(AGENT_HARNESS_ENGINE_PROFILES).toConstantValue({ read });
      bindAgentHarness(container, dependencies());
      expect(await engineAuthorityOf(container)('org-1')).to.deep.equal(
        RELEASE_ENGINE_PROFILES[0],
      );
      expect(read.calledOnce).to.equal(true);
    });

    it('is none when the ceiling cannot be read', async () => {
      const container = new Container();
      container
        .bind(AGENT_HARNESS_ENGINE_PROFILES)
        .toConstantValue({ read: () => Promise.reject(new Error('kv down')) });
      bindAgentHarness(container, dependencies());
      expect(await engineAuthorityOf(container)('org-1')).to.equal(null);
    });
  });
});

describe('agent harness composition: off or partly bound', () => {
  it("the conversation routes' harness middlewares pass every request through", () => {
    const empty = new Container();
    for (const middleware of [
      createActiveRunMiddleware(empty, 'chat'),
      createChatTurnMiddleware(empty, {} as never),
    ]) {
      const next = sinon.spy();
      void middleware({} as never, {} as never, next);
      expect(next.calledOnceWithExactly()).to.equal(true);
    }
  });

  it('stopping background services stops each bound one and tolerates none; the run sweeper goes first (OPS1: runs wait on its leadership)', async () => {
    await stopAgentHarnessBackgroundServices(new Container());

    const container = new Container();
    const order: string[] = [];
    const service = (name: string) => ({
      stop: () => {
        order.push(name);
        return Promise.resolve();
      },
    });
    container.bind(AGENT_HARNESS_ERASURE_WORKER).toConstantValue(service('erasure'));
    container.bind(AGENT_HARNESS_RETENTION_SWEEPER).toConstantValue(service('retention'));
    container.bind(AGENT_HARNESS_CAPABILITIES).toConstantValue(service('capabilities'));
    container.bind(AGENT_HARNESS_AUDIT_ANCHORING).toConstantValue(service('anchoring'));
    container.bind(AGENT_HARNESS_SCHEDULE_FIRER).toConstantValue(service('schedules'));
    container.bind(AGENT_HARNESS_RUN_SWEEPER).toConstantValue(service('runs'));
    await stopAgentHarnessBackgroundServices(container);
    expect(order).to.deep.equal([
      'runs',
      'erasure',
      'retention',
      'capabilities',
      'anchoring',
      'schedules',
    ]);
  });

  it('OPS2-2: stopping again (quiesce, then dispose; or concurrent stops) releases each lease once', async () => {
    const container = new Container();
    let stops = 0;
    container.bind(AGENT_HARNESS_RUN_SWEEPER).toConstantValue({
      stop: async () => {
        stops += 1;
      },
    });
    await Promise.all([
      stopAgentHarnessBackgroundServices(container),
      stopAgentHarnessBackgroundServices(container),
    ]);
    await stopAgentHarnessBackgroundServices(container);
    expect(stops).to.equal(1);
  });
});
