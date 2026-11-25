import { expect } from 'chai';
import sinon from 'sinon';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import {
  HarnessForbiddenError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { SubmitOutcome } from '../../../src/modules/agent-harness/application/run-admission-service';
import { fixedRollout, KILL_SWITCH } from './support/rollout';
import { agentDefinition, grantingAccess } from './support/agent-definition';

const allowAll = grantingAccess();

const admitted = (outcome: SubmitOutcome): unknown => {
  if (outcome.route !== 'durable') expect.fail('expected a durable admission');
  return outcome.admission;
};

describe('RunAdmissionService', () => {
  it('builds server-owned run and dispatch IDs around a canonical payload', async () => {
    const store = {
      answeredRun: async () => null,
      admit: sinon.stub().callsFake(async (command) => ({ command })),
    };
    const service = new RunAdmissionService({
      store: store as never,
      rollout: fixedRollout(),
      clock: { now: () => 1_000 },
      newId: () => 'server-run-id',
      agentAccess: allowAll,
    });
    const input = {
      query: 'find the answer',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'auto' },
    };
    const result = admitted(await service.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer user-token',
      idempotencyKey: 'submit-1',
      deadlineMs: 60_000,
      input,
    })) as never as { command: Record<string, unknown> };

    expect(result.command).to.include({
      runId: 'server-run-id',
      absoluteDeadlineAt: 61_000,
      sessionId: null,
      orgId: 'org-1',
      actorId: 'user-1',
    });
    expect(result.command.dispatchCommandId).to.match(
      /^dispatch:[a-f0-9]{64}$/,
    );
    expect(result.command.payloadHash).to.match(/^[a-f0-9]{64}$/);
  });

  it('changes the payload hash when a retry changes execution input', async () => {
    const store = { answeredRun: async () => null, admit: sinon.stub().resolves({}) };
    const service = new RunAdmissionService({
      store: store as never,
      rollout: fixedRollout(),
      clock: { now: () => 1_000 },
      agentAccess: allowAll,
    });
    const base = {
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer user-token',
      sessionId: 'session-1',
      idempotencyKey: 'same-key',
      deadlineMs: 60_000,
      input: {
        query: 'one',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
    };
    await service.submit(base);
    await service.submit({ ...base, input: { ...base.input, query: 'two' } });
    expect(store.admit.firstCall.args[0].payloadHash).not.to.equal(
      store.admit.secondCall.args[0].payloadHash,
    );
  });

  it('creates a built-in chat command without an agent key', async () => {
    const store = {
      answeredRun: async () => null,
      admit: sinon.stub().callsFake(async (command) => ({ command })),
    };
    const service = new RunAdmissionService({
      store: store as never,
      rollout: fixedRollout(),
      clock: { now: () => 1_000 },
      newId: () => 'chat-run-id',
    });
    const result = admitted(await service.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      idempotencyKey: 'chat-submit-1',
      deadlineMs: 60_000,
      input: {
        query: 'plain chat',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
    })) as never as { command: Record<string, unknown> };

    expect(result.command).not.to.have.property('agentKey');
    expect(result.command).to.include({ runId: 'chat-run-id' });
  });

  describe('agent authorization', () => {
    const request = {
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer user-token',
      idempotencyKey: 'submit-1',
      deadlineMs: 60_000,
      input: {
        query: 'q',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
    };

    const rejection = async (work: Promise<unknown>): Promise<unknown> => {
      try {
        await work;
      } catch (error) {
        return error;
      }
      expect.fail('Expected admission to be rejected');
    };

    it('asks the policy with the actor identity and credential before admitting', async () => {
      const store = { answeredRun: async () => null, admit: sinon.stub().resolves({}) };
      const authorizeRun = sinon.stub().resolves(agentDefinition());
      await new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(),
        agentAccess: { authorizeRun },
      }).submit(request);
      expect(
        authorizeRun.calledOnceWithExactly({
          orgId: 'org-1',
          actorId: 'user-1',
          agentKey: 'agent-1',
          authorization: 'Bearer user-token',
        }),
      ).to.equal(true);
      expect(authorizeRun.calledBefore(store.admit)).to.equal(true);
      expect(store.admit.firstCall.args[0].agentDefinition).to.deep.equal(
        agentDefinition(),
      );
    });

    it('keeps the granted definition off the idempotency hash', async () => {
      const commands: Array<Record<string, unknown>> = [];
      const store = {
        answeredRun: async () => null,
        admit: sinon.stub().callsFake(async (command) => {
          commands.push(command);
          return {};
        }),
      };
      let version = 7;
      const service = new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(),
        clock: { now: () => 1 },
        newId: () => 'run',
        agentAccess: { authorizeRun: async () => agentDefinition({ definitionVersion: version }) },
      });
      await service.submit(request);
      version = 8;
      await service.submit(request);
      expect(commands[0]?.payloadHash).to.equal(commands[1]?.payloadHash);
      expect(commands[0]?.dispatchCommandId).to.equal(
        commands[1]?.dispatchCommandId,
      );
    });

    it('rejects an agent the actor cannot use and writes nothing', async () => {
      const store = { answeredRun: async () => null, admit: sinon.stub() };
      const service = new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(),
        agentAccess: { authorizeRun: sinon.stub().resolves(null) },
      });
      expect(await rejection(service.submit(request))).to.be.instanceOf(
        HarnessForbiddenError,
      );
      const { authorization: _drop, ...withoutCredential } = request;
      _drop;
      expect(
        await rejection(service.submit(withoutCredential)),
      ).to.be.instanceOf(HarnessForbiddenError);
      expect(store.admit.called).to.equal(false);
    });

    it('fails closed when access cannot be verified', async () => {
      const store = { answeredRun: async () => null, admit: sinon.stub() };
      const failing = new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(),
        agentAccess: { authorizeRun: sinon.stub().rejects(new Error('AI backend down')) },
      });
      expect(await rejection(failing.submit(request))).to.be.instanceOf(
        HarnessUnavailableError,
      );
      const unconfigured = new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(),
      });
      expect(await rejection(unconfigured.submit(request))).to.be.instanceOf(
        HarnessUnavailableError,
      );
      expect(store.admit.called).to.equal(false);
    });

    it('does not consult the policy for built-in chat', async () => {
      const store = { answeredRun: async () => null, admit: sinon.stub().resolves({}) };
      const authorizeRun = sinon.stub();
      const { agentKey: _agent, ...chat } = request;
      _agent;
      await new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(),
        agentAccess: { authorizeRun },
      }).submit(chat);
      expect(authorizeRun.called).to.equal(false);
      expect(store.admit.calledOnce).to.equal(true);
      expect(store.admit.firstCall.args[0]).not.to.have.property('agentDefinition');
    });
  });

  describe('rollout', () => {
    const request = {
      orgId: 'org-1',
      actorId: 'user-1',
      idempotencyKey: 'submit-1',
      deadlineMs: 60_000,
      input: {
        query: 'q',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto', modelKey: 'model-1' },
      },
    };

    it('asks the policy with the org and the requested model', async () => {
      const decide = sinon.stub().resolves({
        useHarness: true,
        harnessMajor: 1,
        reason: 'enabled',
      });
      const store = { answeredRun: async () => null, admit: sinon.stub().resolves({}) };
      await new RunAdmissionService({
        store: store as never,
        rollout: { decide, decideForOrg: sinon.stub() },
      }).submit(request);
      expect(
        decide.calledOnceWithExactly({
          orgId: 'org-1',
          model: { chatMode: 'auto', modelKey: 'model-1' },
          assistant: true,
        }),
      ).to.equal(true);
    });

    it('tells the policy an agent run is not the built-in assistant', async () => {
      const decide = sinon.stub().resolves({
        useHarness: false,
        harnessMajor: 2,
        reason: 'chat_mode_not_supported',
      });
      const store = { findAdmission: sinon.stub().resolves(null) };
      await new RunAdmissionService({
        store: store as never,
        rollout: { decide, decideForOrg: sinon.stub() },
      }).submit({ ...request, agentKey: 'agent-1' });
      expect(decide.firstCall.args[0]).to.include({ assistant: false });
    });

    it('pins the decided harness major on the admitted command', async () => {
      const store = {
        answeredRun: async () => null,
        admit: sinon.stub().callsFake(async (command) => ({ command })),
      };
      const result = admitted(
        await new RunAdmissionService({
          store: store as never,
          rollout: fixedRollout(),
        }).submit(request),
      ) as { command: Record<string, unknown> };
      expect(result.command.harnessMajor).to.equal(1);
    });

    it('routes to legacy without writing or authorizing when the policy says no', async () => {
      const store = {
        answeredRun: async () => null,
        admit: sinon.stub(),
        findAdmission: sinon.stub().resolves(null),
      };
      const authorizeRun = sinon.stub().resolves(agentDefinition());
      const outcome = await new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(() => KILL_SWITCH),
        agentAccess: { authorizeRun },
      }).submit({ ...request, agentKey: 'agent-1', authorization: 'Bearer t' });
      expect(outcome).to.deep.equal({ route: 'legacy', rollout: KILL_SWITCH });
      expect(store.admit.called).to.equal(false);
      expect(authorizeRun.called).to.equal(false);
    });

    it('replays an admission made before the policy changed', async () => {
      const previous = { sessionId: 's', created: false };
      const store = {
        answeredRun: async () => null,
        admit: sinon.stub(),
        findAdmission: sinon.stub().resolves(previous),
      };
      const outcome = await new RunAdmissionService({
        store: store as never,
        rollout: fixedRollout(() => KILL_SWITCH),
      }).submit(request);
      expect(outcome).to.deep.equal({ route: 'durable', admission: previous });
      expect(store.findAdmission.firstCall.args[0]).to.include({
        orgId: 'org-1',
        actorId: 'user-1',
        idempotencyKey: 'submit-1',
        sessionId: null,
      });
      expect(store.admit.called).to.equal(false);
    });

    it('fails closed when the policy cannot be read', async () => {
      const store = { answeredRun: async () => null, admit: sinon.stub(), findAdmission: sinon.stub() };
      try {
        await new RunAdmissionService({
          store: store as never,
          rollout: {
            decide: sinon.stub().rejects(new Error('KV unavailable')),
            decideForOrg: sinon.stub(),
          },
        }).submit(request);
        expect.fail('expected unavailable');
      } catch (error) {
        expect(error).to.be.instanceOf(HarnessUnavailableError);
      }
      expect(store.admit.called).to.equal(false);
      expect(store.findAdmission.called).to.equal(false);
    });
  });
});
