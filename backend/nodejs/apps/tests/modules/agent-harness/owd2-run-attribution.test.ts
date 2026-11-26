/**
 * OWD-2 (D11, SEC-AZ-04): every admitted run records who it acts as (a person
 * or an agent's service account), who started it and on whose authority, taken
 * from verified auth only. The run keeps it, a child keeps its parent's, and
 * `run.admitted`'s audit record carries it, so a service account's runs are
 * labelled in the audit chain. Workers and streams are unchanged.
 */
import { expect } from 'chai';
import type { Request } from 'express';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import {
  InMemoryJournalStore,
  InMemoryPayloadStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { childAdmittedEvent } from '../../../src/modules/agent-harness/domain/child-runs';
import { HarnessForbiddenError } from '../../../src/modules/agent-harness/domain/errors';
import {
  admittedJournalAppend,
  HarnessRun,
} from '../../../src/modules/agent-harness/domain/execution';
import { runAttribution } from '../../../src/modules/agent-harness/domain/principal';
import { workerRun } from '../../../src/modules/agent-harness/domain/steering';
import {
  requestPrincipalKind,
  requestVia,
} from '../../../src/modules/agent-harness/transport/request-identity';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { HARNESS_ENABLED } from './support/rollout';

const verified = (user: Record<string, unknown>, body: unknown = {}): Request =>
  ({ user, body, headers: {} }) as unknown as Request;

describe('OWD-2 run attribution: principal, initiator and on whose behalf', () => {
  describe('from verified auth only', () => {
    it("OWD-2: a person's run acts as them, started by them, on their own authority", () => {
      expect(runAttribution({ actorId: 'u1', principalKind: 'user', agentKey: 'a1' })).to.deep.equal({
        principalKind: 'user',
        initiatorRef: 'p_u1',
        onBehalfOf: 'p_u1',
      });
    });

    it('OWD-2: a scheduled run is started by its schedule and acts on its creator’s authority', () => {
      expect(
        runAttribution({ actorId: 'u1', principalKind: 'user', trigger: { scheduleId: 'sched-9' } }),
      ).to.deep.equal({ principalKind: 'user', initiatorRef: 'trigger:sched-9', onBehalfOf: 'p_u1' });
    });

    it("SEC-AZ-04: a service account's run is the person's turn on the agent's own authority", () => {
      expect(
        runAttribution({ actorId: 'ext-7', principalKind: 'agent_service', agentKey: 'support-bot' }),
      ).to.deep.equal({ principalKind: 'agent_service', initiatorRef: 'p_ext-7', onBehalfOf: 'agent:support-bot' });
      expect(() => runAttribution({ actorId: 'ext-7', principalKind: 'agent_service' })).to.throw(
        HarnessForbiddenError,
      );
    });

    it('OWD-2: only the verified identity makes a service account; the body never does', () => {
      expect(requestPrincipalKind(verified({ userId: 'u1', isServiceAccount: true }))).to.equal('agent_service');
      expect(requestVia(verified({ userId: 'u1', isServiceAccount: true }))).to.equal('service');
      const claimed = verified(
        { userId: 'u1' },
        { isServiceAccount: true, principalKind: 'agent_service', onBehalfOf: 'agent:x' },
      );
      expect(requestPrincipalKind(claimed)).to.equal('user');
      expect(requestPrincipalKind(verified({ userId: 'u1', isServiceAccount: 'true' }))).to.equal('user');
    });
  });

  describe('on the admitted run', () => {
    const setup = () => {
      const clock = contractClock();
      const h = createContractHarness(inMemoryBackend(clock), clock);
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      return h;
    };

    it("SEC-AZ-04: admission keeps a service account's attribution on the run, out of the worker's view", async () => {
      const h = setup();
      const admitted = await h.admit({
        agentKey: 'support-bot',
        authorization: 'Bearer svc',
        via: 'service',
        principalKind: 'agent_service',
      });
      expect(admitted.run.attribution).to.deep.equal({
        principalKind: 'agent_service',
        initiatorRef: `p_${h.tenant.actorId}`,
        onBehalfOf: 'agent:support-bot',
      });
      expect(workerRun(admitted.run)).to.not.have.property('attribution');
      const [admittedEvent] = await h.backend.journal.readRun(admitted.run.orgId, admitted.run.runId, 0, 10);
      expect(admittedEvent?.type).to.equal('run.admitted');
      expect(admittedEvent).to.not.have.property('auditFields');
    });

    it('OWD-2: a person is the default; a service account without its agent is refused and nothing is written', async () => {
      const h = setup();
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      expect(admitted.run.attribution).to.deep.include({ principalKind: 'user', onBehalfOf: `p_${h.tenant.actorId}` });
      let refused: unknown;
      try {
        await h.submit({ principalKind: 'agent_service' });
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessForbiddenError);
    });
  });

  describe('in the audit chain', () => {
    const run = (attribution?: HarnessRun['attribution']): HarnessRun =>
      ({
        runId: 'run-1',
        orgId: 'org-1',
        sessionId: 'session-1',
        actorId: 'ext-7',
        input: null,
        ...(attribution !== undefined ? { attribution } : {}),
      }) as unknown as HarnessRun;

    const record = async (events: Parameters<AuditedJournalStore<unknown>['append']>[0]) => {
      const audit = new InMemoryAuditChainStore();
      const raw = new InMemoryJournalStore();
      await new AuditedJournalStore(raw, audit).append(
        await new JournalService(new InMemoryPayloadStore(), testCipher()).prepare(events),
        undefined,
      );
      return { audit: await audit.read('org-1', 0, 10), stored: await raw.readRun('org-1', 'run-1', 0, 10) };
    };

    it("SEC-AZ-04: run.admitted's audit record labels a service account and names initiator and authority", async () => {
      const attribution = runAttribution({ actorId: 'ext-7', principalKind: 'agent_service', agentKey: 'support-bot' });
      const { audit, stored } = await record(admittedJournalAppend(run(attribution), 'service'));
      expect(audit.map((entry) => entry.kind)).to.deep.equal(['run.admitted']);
      expect(audit[0]?.fields).to.deep.equal({
        runKind: 'interactive',
        principalRef: 'p_ext-7',
        via: 'service',
        principalKind: 'agent_service',
        initiatorRef: 'p_ext-7',
        onBehalfOf: 'agent:support-bot',
      });
      // The journal event, which workers fold, is exactly what it was before OWD-2.
      expect(stored[0]?.data).to.deep.equal({ runKind: 'interactive', principalRef: 'p_ext-7', via: 'service' });
      expect(stored[0]).to.not.have.property('auditFields');
    });

    it("OWD-2: a child's audit record carries its parent's attribution unchanged", async () => {
      const attribution = runAttribution({ actorId: 'ext-7', principalKind: 'agent_service', agentKey: 'support-bot' });
      const parent = { runId: 'run-0', actorId: 'ext-7', attribution };
      const { audit } = await record({
        orgId: 'org-1',
        sessionId: 'session-1',
        streamId: 'run-1',
        runId: 'run-1',
        writer: 'node',
        events: [childAdmittedEvent(parent, 'service')],
      });
      expect(audit[0]?.fields).to.deep.include({
        principalRef: 'p_ext-7',
        parentRunId: 'run-0',
        principalKind: 'agent_service',
        onBehalfOf: 'agent:support-bot',
      });
    });

    it('OWD-2: a run admitted before attribution existed audits as before', async () => {
      const { audit } = await record(admittedJournalAppend(run(), 'session'));
      expect(audit[0]?.fields).to.deep.equal({ runKind: 'interactive', principalRef: 'p_ext-7', via: 'session' });
    });
  });
});
