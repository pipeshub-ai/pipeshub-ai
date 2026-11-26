import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_APPROVAL_QUEUE,
  AGENT_HARNESS_BINDERS,
  AGENT_HARNESS_CHILD_RUNS,
  AGENT_HARNESS_DATA_KEYS,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_MEMORIES,
  AgentHarnessDependencies,
  bindAgentHarness,
  CONTROL_PLANE_TRANSACTION,
  HarnessBinder,
  HarnessBindingContext,
} from '../../../src/modules/agent-harness/composition';
import { MongoExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/mongo';

const dependencies = (): AgentHarnessDependencies =>
  ({
    sessions: {},
    conversations: {},
    history: {},
    agentAccess: {},
    rollout: {},
    authority: {},
    keyEncryptionKey: {},
    memory: { policies: { read: async () => ({ value: null, revision: null }) } },
  }) as unknown as AgentHarnessDependencies;

/** Each service that reads runs, with the field it keeps its execution store in. */
const RUN_READERS: ReadonlyArray<[string, string]> = [
  [AGENT_HARNESS_CHILD_RUNS, 'runs'],
  [AGENT_HARNESS_EXECUTION_SERVICE, 'store'],
  [AGENT_HARNESS_MEMORIES, 'runs'],
  [AGENT_HARNESS_APPROVAL_QUEUE, 'runs'],
];

const storesOf = (container: Container): unknown[] =>
  RUN_READERS.map(
    ([token, field]) => (container.get(token) as Record<string, unknown>)[field],
  );

describe('WP-8 composition binders', () => {
  it('WP8-3 binds the execution store once: every service that reads runs holds that one store', () => {
    const container = new Container();
    bindAgentHarness(container, dependencies());
    const store = container.get(AGENT_HARNESS_EXECUTION_STORE);
    expect(store).to.be.instanceOf(MongoExecutionStore);
    for (const held of storesOf(container)) expect(held).to.equal(store);
  });

  it('WP8-4 keeps an execution store bound before it, and every reader uses that one', () => {
    const container = new Container();
    const double = { double: true };
    container.bind(AGENT_HARNESS_EXECUTION_STORE).toConstantValue(double);
    bindAgentHarness(container, dependencies());
    expect(container.get(AGENT_HARNESS_EXECUTION_STORE)).to.equal(double);
    for (const held of storesOf(container)) expect(held).to.equal(double);
  });

  describe('WP8-5 one more binder (the CPD-4 seam)', () => {
    it('sees the shared journal and control-plane transaction and binds its own token', () => {
      const container = new Container();
      const seen: HarnessBindingContext[] = [];
      const bindPolicyStore: HarnessBinder = (context) => {
        seen.push(context);
        context.container.bind('PolicyStore').toConstantValue({ policies: true });
      };
      bindAgentHarness(container, dependencies(), [
        ...AGENT_HARNESS_BINDERS,
        bindPolicyStore,
      ]);
      expect(seen).to.have.length(1);
      expect(seen[0]!.journal).to.equal(container.get(AGENT_HARNESS_DATA_KEYS));
      expect(seen[0]!.controlPlane).to.equal(
        container.get(CONTROL_PLANE_TRANSACTION),
      );
      expect(container.get('PolicyStore')).to.deep.equal({ policies: true });
      expect(container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)).to.equal(true);
    });

    it('placed first, its store is what the later binders use', () => {
      const container = new Container();
      const store = { fromBinder: true };
      const bindStore: HarnessBinder = ({ container: c }) => {
        c.bind(AGENT_HARNESS_EXECUTION_STORE).toConstantValue(store);
      };
      bindAgentHarness(container, dependencies(), [
        bindStore,
        ...AGENT_HARNESS_BINDERS,
      ]);
      for (const held of storesOf(container)) expect(held).to.equal(store);
    });
  });
});
