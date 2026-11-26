import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { NextRunService } from '../../../src/modules/agent-harness/application/next-run-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { SessionInteractionService } from '../../../src/modules/agent-harness/application/session-interactions';
import { HarnessDecisionRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { PersonDecision } from '../../../src/modules/agent-harness/domain/interactions';
import { SessionExecutionScope } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import { describeCollaboratorTurnsContract } from './support/collaborator-turns-contract';
import { testCipher } from './support/data-protection';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { readmissionBy } from './support/next-runs';

describeCollaboratorTurnsContract('in-memory', (clock) => {
  const payloads = new InMemoryPayloadStore();
  const store = new InMemoryExecutionStore(
    clock,
    undefined,
    undefined,
    null,
    new JournalService(payloads, testCipher()),
  );
  return {
    executionStore: store,
    journal: store.journal,
    payloads,
    deletePayload: async (_orgId, ref) => {
      payloads.objects.delete(ref);
    },
    admissionStore: store,
    roles: store,
    share: async (orgId, sessionId, userId, accessLevel) => {
      store.shareSession(orgId, sessionId, userId, accessLevel);
    },
    unshare: async (orgId, sessionId, userId) => {
      store.unshareSession(orgId, sessionId, userId);
    },
    dispatches: async (orgId, runId) =>
      store.dispatches
        .filter((item) => item.orgId === orgId && item.runId === runId)
        .map(({ commandId, kind }) => ({ commandId, kind })),
  };
});

describe('approvals in a shared session, two users (SS2 AP-1, AP-2)', () => {
  const ORG = new Types.ObjectId().toString();
  const [alice, bob, carol, approver] = [0, 1, 2, 3].map(() =>
    new Types.ObjectId().toString(),
  ) as [string, string, string, string];
  let store: InMemoryExecutionStore;
  let resolved: Array<{ by: string; decision: string }>;
  let alicesRun: HarnessRun;

  const send = async (actorId: string, sessionId?: string): Promise<HarnessRun> => {
    const outcome = await new RunAdmissionService({
      store,
      rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      roles: store,
      collaboratorTurns: { enabled: async () => true },
    }).submit({
      orgId: ORG,
      actorId,
      ...(sessionId !== undefined ? { sessionId } : {}),
      idempotencyKey: randomUUID(),
      deadlineMs: 9_000,
      input: {
        query: 'q',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'internal_search' },
      },
    });
    if (outcome.route !== 'durable') return expect.fail('durable expected');
    return outcome.admission.run;
  };

  /** The real decision service over the store's roles; the resolution itself is recorded. */
  const decide = (run: HarnessRun, actorId: string, decision: PersonDecision) =>
    new SessionInteractionService({
      executions: { get: (orgId: string, runId: string) => store.get(orgId, runId) } as never,
      interactions: {
        resolve: async (command: { by: string; decision: string }) => {
          resolved.push({ by: command.by, decision: command.decision });
          return {};
        },
      } as never,
      sessions: {
        findStreamAccess: async (scope: SessionExecutionScope) =>
          (await store.roleOf(scope)) === null ? null : { audience: 'shared' },
      } as never,
      roles: store,
      approvers: new InMemoryApproverDirectory(new Map([[ORG, new Set([approver])]]), new Map()),
    })
      .decide({
        orgId: ORG,
        actorId,
        sessionId: run.sessionId,
        runId: run.runId,
        interactionId: 'ix.approve.t1.0',
        decision,
        idempotencyKey: randomUUID(),
        channel: 'web',
      })
      .then(
        () => 'ok',
        (error: unknown) =>
          error instanceof HarnessDecisionRefusedError ? error.code : String(error),
      );

  beforeEach(async () => {
    store = new InMemoryExecutionStore();
    resolved = [];
    alicesRun = await send(alice);
    store.shareSession(ORG, alicesRun.sessionId, bob, 'write');
    store.shareSession(ORG, alicesRun.sessionId, carol, 'read');
    // The decision route serves participants (P2.6c): the approver reads the session too.
    store.shareSession(ORG, alicesRun.sessionId, approver, 'read');
  });

  it("AP-1: Bob's run: only Bob approves; Alice, the owner, may only deny", async () => {
    await store.failRun(ORG, alicesRun.runId, 'ended', []);
    const bobsRun = await send(bob, alicesRun.sessionId);
    expect(bobsRun.actorId).to.equal(bob);
    expect(await decide(bobsRun, alice, 'approved')).to.equal('APPROVER_REQUIRED');
    expect(await decide(bobsRun, carol, 'denied')).to.equal('NOT_ALLOWED_TO_DECIDE');
    expect(await decide(bobsRun, alice, 'denied')).to.equal('ok');
    expect(await decide(bobsRun, bob, 'approved')).to.equal('ok');
    expect(await decide(bobsRun, approver, 'approved')).to.equal('ok');
    expect(resolved).to.deep.equal([
      { by: `p_${alice}`, decision: 'denied' },
      { by: `p_${bob}`, decision: 'approved' },
      { by: `p_${approver}`, decision: 'approved' },
    ]);
  });

  it("AP-2: Alice's run: Bob, a collaborator, may neither approve nor deny", async () => {
    expect(await decide(alicesRun, bob, 'approved')).to.equal('APPROVER_REQUIRED');
    expect(await decide(alicesRun, bob, 'denied')).to.equal('NOT_ALLOWED_TO_DECIDE');
    expect(await decide(alicesRun, alice, 'approved')).to.equal('ok');
  });
});

describe('logs of collaborator turns and next runs (SS2 NR-L, CR-3)', () => {
  it('name ids, roles and reasons; never the message', async () => {
    const lines: Array<[string, Record<string, unknown>]> = [];
    const record = (message: string, meta: Record<string, unknown>) => {
      lines.push([message, meta]);
    };
    const logger = { debug: () => undefined, info: record, warn: record, error: record };
    const store = new InMemoryExecutionStore();
    const org = new Types.ObjectId().toString();
    const alice = new Types.ObjectId().toString();
    const bob = new Types.ObjectId().toString();
    const service = new RunAdmissionService({
      store,
      rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      roles: store,
      collaboratorTurns: { enabled: async () => true },
      logger,
    });
    const send = (actorId: string, query: string, sessionId?: string) =>
      service.submit({
        orgId: org,
        actorId,
        ...(sessionId !== undefined ? { sessionId } : {}),
        idempotencyKey: randomUUID(),
        deadlineMs: 9_000,
        input: {
          query,
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: 'internal_search' },
        },
      });
    const first = await send(alice, 'SECRET-ALICE merger terms');
    if (first.route !== 'durable') return expect.fail('durable expected');
    const sessionId = first.admission.sessionId;
    store.shareSession(org, sessionId, bob, 'write');
    await send(bob, 'SECRET-BOB salary question', sessionId);
    await store.failRun(org, first.admission.run.runId, 'ended', []);
    const drains = new NextRunService({
      store,
      readmission: readmissionBy(() => service),
      collaboratorTurns: { enabled: async () => true },
      logger,
    });
    expect(await drains.drain(org, sessionId)).to.equal('admitted');
    const queued = lines.find(([message]) => message === 'input.next_run_queued')?.[1];
    expect(queued).to.include({ actorId: bob, position: 1, sessionId });
    const admitted = lines.find(([message]) => message === 'next_run.admitted')?.[1];
    expect(admitted).to.include({ actorId: bob, sessionRole: 'collaborator' });
    expect(JSON.stringify(lines)).to.not.match(/SECRET-/);
  });
});
