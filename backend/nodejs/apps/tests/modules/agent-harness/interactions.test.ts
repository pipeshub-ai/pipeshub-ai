/**
 * P2.6c: who may decide an approval (NA-*), the pure open-interaction rules
 * the stores apply (NR-8, NR-10), and the wake channel the worker listens on.
 * The store behaviour itself is in the execution-store contract (NR-1..NR-10).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  DecideInteraction,
  decisionCommandId,
  SessionInteractionService,
} from '../../../src/modules/agent-harness/application/session-interactions';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { wakeChannel } from '../../../src/modules/agent-harness/adapters/redis/redis-run-wakeup';
import {
  HarnessConflictError,
  HarnessDecisionRefusedError,
  HarnessNotFoundError,
} from '../../../src/modules/agent-harness/domain/errors';
import {
  CommitBatch,
  HarnessRun,
} from '../../../src/modules/agent-harness/domain/execution';
import {
  assertResolvable,
  assertSuspendable,
  Decider,
  decisionRefusal,
  openAfterCommit,
  PersonDecision,
  resolutionJournalEvents,
} from '../../../src/modules/agent-harness/domain/interactions';
import { principalRefFor } from '../../../src/modules/agent-harness/domain/run-spec';
import { SessionRole } from '../../../src/modules/agent-harness/domain/session-roles';
import { workerRun } from '../../../src/modules/agent-harness/domain/steering';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { decideSessionInteractionSchema } from '../../../src/modules/agent-harness/transport/schemas';

const decider = (overrides: Partial<Decider>): Decider => ({
  isRunActor: false,
  isApprover: false,
  role: null,
  isOrgAdmin: false,
  ...overrides,
});

describe('approval authority (P2.6c NA-1, ARCHITECTURE 4.9)', () => {
  const people: Array<[string, Decider, string | null, string | null]> = [
    // who, decider, refusal to approve, refusal to deny
    ['the run actor', decider({ isRunActor: true, role: 'owner' }), null, null],
    ['a named approver', decider({ isApprover: true, role: 'viewer' }), null, null],
    ['the session owner who is not the actor', decider({ role: 'owner' }), 'APPROVER_REQUIRED', null],
    ['an org admin', decider({ isOrgAdmin: true }), 'APPROVER_REQUIRED', null],
    ['a collaborator', decider({ role: 'collaborator' }), 'APPROVER_REQUIRED', 'NOT_ALLOWED_TO_DECIDE'],
    ['a viewer', decider({ role: 'viewer' }), 'APPROVER_REQUIRED', 'NOT_ALLOWED_TO_DECIDE'],
    ['a stranger', decider({}), 'APPROVER_REQUIRED', 'NOT_ALLOWED_TO_DECIDE'],
  ];
  for (const [who, person, approve, deny] of people) {
    it(`${who}: approve ${approve ?? 'allowed'}, deny ${deny ?? 'allowed'}`, () => {
      expect(decisionRefusal(person, 'approved')).to.equal(approve);
      expect(decisionRefusal(person, 'denied')).to.equal(deny);
    });
  }
});

describe('open interactions (P2.6c NR-8, NR-10)', () => {
  const requested = (interactionId: string): CommitBatch['events'][number] => ({
    type: 'interaction.requested',
    schemaVersion: 1,
    critical: true,
    visibility: 'owner',
    data: { interactionId, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` },
  });
  const suspend: CommitBatch['transition'] = {
    status: 'waiting_approval',
    detail: { kind: 'suspend', reason: 'approval', interactionId: 'ix-a' },
  };

  it('a commit adds what it requests; an untracked run stays untracked', () => {
    expect(openAfterCommit({}, { events: [] })).to.equal(undefined);
    expect(openAfterCommit({}, { events: [requested('ix-a')] })).to.deep.equal(['ix-a']);
    expect(
      openAfterCommit({ openInteractions: ['ix-a'] }, { events: [requested('ix-a'), requested('ix-b')] }),
    ).to.deep.equal(['ix-a', 'ix-b']);
  });

  it('a suspend for approval with nothing left open is refused; others are not', () => {
    const refused = () => assertSuspendable({ openInteractions: [] }, { events: [], transition: suspend });
    expect(refused).to.throw(HarnessConflictError).with.property('code', 'INTERACTION_RESOLVED');
    expect(() => assertSuspendable({ openInteractions: ['ix-a'] }, { events: [], transition: suspend })).not.to.throw();
    expect(() => assertSuspendable({ openInteractions: [] }, { events: [requested('ix-b')], transition: suspend })).not.to.throw();
    expect(() => assertSuspendable({}, { events: [], transition: suspend })).not.to.throw();
    expect(() =>
      assertSuspendable(
        { openInteractions: [] },
        { events: [], transition: { status: 'paused', detail: { kind: 'suspend', reason: 'input' } } },
      ),
    ).not.to.throw();
  });

  it('a resolution resumes only a suspended run', () => {
    const resolution = { interactionId: 'ix-a', decision: 'approved' as const, channel: 'web' as const };
    expect(resolutionJournalEvents({ status: 'running' }, resolution, 'p_u').map((e) => e.type)).to.deep.equal([
      'interaction.resolved',
    ]);
    expect(resolutionJournalEvents({ status: 'queued' }, resolution, 'p_u').map((e) => e.type)).to.deep.equal([
      'interaction.resolved',
    ]);
    expect(
      resolutionJournalEvents({ status: 'waiting_approval' }, resolution, 'p_u').map((e) => e.type),
    ).to.deep.equal(['interaction.resolved', 'run.resumed']);
  });

  it('cancelling, ended and unopened runs refuse a resolution with a reason', () => {
    const code = (run: Parameters<typeof assertResolvable>[0], ix = 'ix-a'): string => {
      try {
        assertResolvable(run, ix);
      } catch (error) {
        return (error as HarnessConflictError).code;
      }
      return 'none';
    };
    expect(code({ harnessMajor: 2, status: 'cancelling', openInteractions: ['ix-a'] })).to.equal('RUN_CANCELLING');
    expect(code({ harnessMajor: 2, status: 'completed' })).to.equal('INTERACTION_NOT_OPEN');
    expect(code({ harnessMajor: 2, status: 'running', openInteractions: ['ix-b'] })).to.equal('INTERACTION_NOT_OPEN');
    expect(code({ harnessMajor: 2, status: 'running', openInteractions: ['ix-a'] })).to.equal('none');
    expect(code({ harnessMajor: 2, status: 'running' })).to.equal('none');
    expect(code({ harnessMajor: 1, status: 'running' })).to.equal('INVALID_TRANSITION');
  });

  it('the worker never sees the bookkeeping (its run model is strict)', () => {
    const run = { runId: 'r', openInteractions: ['ix-a'] } as unknown as HarnessRun;
    expect(workerRun(run)).not.to.have.property('openInteractions');
  });

  it('the wake channel is the one the worker subscribes to', () => {
    expect(wakeChannel('run-1')).to.equal('harness:interaction-notify:run-1');
    expect(wakeChannel('run-1', 'tenant-a')).to.equal('tenant-a:harness:interaction-notify:run-1');
  });
});

describe('deciding through a session (P2.6c NA-2..NA-5)', () => {
  const ORG = 'org-1';
  const ACTOR = 'u-actor';
  const run = {
    orgId: ORG,
    sessionId: 'ses-1',
    runId: 'run-1',
    actorId: ACTOR,
    harnessMajor: 2,
    status: 'running',
  } as unknown as HarnessRun;
  const receipt = { commandId: 'c', run, firstEventSeq: 1, lastEventSeq: 1 };

  const world = (options: {
    role?: SessionRole | null;
    audience?: 'owner' | 'shared' | null;
    approvers?: string[];
    admins?: string[];
  }) => {
    const resolve = sinon.stub().resolves(receipt);
    const warn = sinon.stub();
    const service = new SessionInteractionService({
      executions: { get: sinon.stub().resolves(run) } as never,
      interactions: { resolve } as never,
      sessions: {
        findStreamAccess: sinon
          .stub()
          .resolves(options.audience === null ? null : { audience: options.audience ?? 'owner' }),
      } as never,
      roles: { roleOf: sinon.stub().resolves(options.role ?? null) },
      approvers: new InMemoryApproverDirectory(
        new Map([[ORG, new Set(options.approvers ?? [])]]),
        new Map([[ORG, new Set(options.admins ?? [])]]),
      ),
      logger: { debug: () => undefined, info: () => undefined, warn, error: () => undefined },
    });
    return { service, resolve, warn };
  };
  const command = (actorId: string, decision: PersonDecision, sessionId = 'ses-1'): DecideInteraction => ({
    orgId: ORG,
    actorId,
    sessionId,
    runId: 'run-1',
    interactionId: 'ix.approve.t1.0',
    decision,
    idempotencyKey: 'k-1',
    channel: 'web',
  });

  it('NA-4: the run actor approves; the decision is journaled as them, with a stable command id', async () => {
    const { service, resolve } = world({ role: 'owner' });
    const decided = await service.decide(command(ACTOR, 'approved'));
    expect(decided.interaction).to.deep.equal({ interactionId: 'ix.approve.t1.0', decision: 'approved' });
    const sent = resolve.firstCall.args[0];
    expect(sent).to.include({ by: principalRefFor(ACTOR), decision: 'approved', channel: 'web' });
    expect(sent.commandId).to.equal(decisionCommandId(command(ACTOR, 'approved')));
    expect(decisionCommandId(command(ACTOR, 'approved'))).not.to.equal(
      decisionCommandId(command('u-other', 'approved')),
    );
  });

  it('NA-4: a named approver approves another person’s run', async () => {
    const { service, resolve } = world({ role: 'viewer', audience: 'shared', approvers: ['u-approver'] });
    await service.decide(command('u-approver', 'approved'));
    expect(resolve.calledOnce).to.equal(true);
  });

  it('NA-1, NA-5: the owner may deny but not approve for the actor; the refusal is logged once with its reason', async () => {
    const { service, resolve, warn } = world({ role: 'owner' });
    const refused = await service.decide(command('u-owner', 'approved')).catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessDecisionRefusedError);
    expect((refused as HarnessDecisionRefusedError).code).to.equal('APPROVER_REQUIRED');
    expect(warn.callCount).to.equal(1);
    expect(warn.firstCall.args[1]).to.include({ reason: 'APPROVER_REQUIRED', interactionId: 'ix.approve.t1.0' });
    expect(JSON.stringify(warn.firstCall.args[1])).not.to.include('arguments');
    expect(resolve.called).to.equal(false);
    await service.decide(command('u-owner', 'denied'));
    expect(resolve.calledOnce).to.equal(true);
  });

  it('NA-1: an admin denies; a collaborator may do neither', async () => {
    const admin = world({ role: null, admins: ['u-admin'] });
    await admin.service.decide(command('u-admin', 'denied'));
    expect(admin.resolve.calledOnce).to.equal(true);
    const collaborator = world({ role: 'collaborator', audience: 'shared' });
    const refused = await collaborator.service
      .decide(command('u-collab', 'denied'))
      .catch((error: unknown) => error);
    expect((refused as HarnessDecisionRefusedError).code).to.equal('NOT_ALLOWED_TO_DECIDE');
  });

  it('NA-2: not a participant, or a run of another session, is not found', async () => {
    const stranger = world({ audience: null });
    const notFound = await stranger.service.decide(command(ACTOR, 'approved')).catch((e: unknown) => e);
    expect(notFound).to.be.instanceOf(HarnessNotFoundError);
    const elsewhere = world({ role: 'owner' });
    const other = await elsewhere.service
      .decide(command(ACTOR, 'approved', 'ses-2'))
      .catch((e: unknown) => e);
    expect(other).to.be.instanceOf(HarnessNotFoundError);
  });

  it('NA-3: the body names the decision and an idempotency key, nothing else', () => {
    const params = { agentKey: 'a1', sessionId: 's1', runId: 'r1', interactionId: 'ix.approve.t1.0' };
    const ok = { decision: 'approved', idempotencyKey: 'k1' };
    expect(decideSessionInteractionSchema.safeParse({ params, body: ok }).success).to.equal(true);
    for (const body of [
      { decision: 'maybe', idempotencyKey: 'k1' },
      { decision: 'approved' },
      { ...ok, by: 'someone-else' },
    ]) {
      expect(decideSessionInteractionSchema.safeParse({ params, body }).success).to.equal(false);
    }
    expect(
      decideSessionInteractionSchema.safeParse({ params: { ...params, interactionId: 'bad id' }, body: ok }).success,
    ).to.equal(false);
  });

  describe("GA-07 (G-12): only the run's actor says an item is connected", () => {
    const connect = (actorId: string, decision: PersonDecision): DecideInteraction => ({
      ...command(actorId, decision),
      interactionId: 'ix.connect.t1.0',
    });

    it('GA-07: the actor says it is connected: resolved approved, which resumes the run', async () => {
      const { service, resolve } = world({ role: 'owner' });
      await service.decide(connect(ACTOR, 'approved'));
      expect(resolve.firstCall.args[0]).to.include({ interactionId: 'ix.connect.t1.0', decision: 'approved' });
    });

    it('GA-07: someone the directory names an approver, or an org admin, may not say it for them; nothing is written', async () => {
      for (const [who, options] of [
        ['u-approver', { role: 'viewer', audience: 'shared', approvers: ['u-approver'] }],
        ['u-admin', { role: null, admins: ['u-admin'] }],
      ] as const) {
        const { service, resolve } = world(options);
        const refused = await service.decide(connect(who, 'approved')).catch((error: unknown) => error);
        expect((refused as HarnessDecisionRefusedError).code).to.equal('CONNECT_OWNER_REQUIRED');
        expect(resolve.called).to.equal(false);
      }
    });

    it('GA-07: an org admin may still decline it, as they may deny an approval', async () => {
      const { service, resolve } = world({ role: null, admins: ['u-admin'] });
      await service.decide(connect('u-admin', 'denied'));
      expect(resolve.firstCall.args[0]).to.include({ interactionId: 'ix.connect.t1.0', decision: 'denied' });
    });
  });

  describe('an unknown effect is never re-called from the conversation; the run actor says whether it happened (H1 UX-1..UX-4, P2.9)', () => {
    const unknown = (actorId: string, decision: PersonDecision): DecideInteraction => ({
      ...command(actorId, decision),
      interactionId: 'ix.unknown.t1.0',
    });

    it('UX-1: the run actor may not approve a re-call from the conversation; nothing is written, logged once', async () => {
      const { service, resolve, warn } = world({ role: 'owner' });
      const refused = await service.decide(unknown(ACTOR, 'approved')).catch((error: unknown) => error);
      expect(refused).to.be.instanceOf(HarnessDecisionRefusedError);
      expect((refused as HarnessDecisionRefusedError).code).to.equal('OPERATOR_REQUIRED');
      expect(resolve.called).to.equal(false);
      expect(warn.callCount).to.equal(1);
      expect(warn.firstCall.args[1]).to.include({ reason: 'OPERATOR_REQUIRED', interactionId: 'ix.unknown.t1.0' });
    });

    it('UX-2: neither may someone the directory names an approver, or an org admin, through the session', async () => {
      for (const [who, options] of [
        ['u-approver', { role: 'viewer', audience: 'shared', approvers: ['u-approver'] }],
        ['u-admin', { role: null, admins: ['u-admin'] }],
      ] as const) {
        const { service, resolve } = world(options);
        const refused = await service.decide(unknown(who, 'approved')).catch((error: unknown) => error);
        expect((refused as HarnessDecisionRefusedError).code).to.equal('OPERATOR_REQUIRED');
        expect(resolve.called).to.equal(false);
      }
    });

    it('UX-3: the actor may still decline it (never re-called, P2.9 KO-4)', async () => {
      const { service, resolve } = world({ role: 'owner' });
      await service.decide(unknown(ACTOR, 'denied'));
      expect(resolve.firstCall.args[0]).to.include({ interactionId: 'ix.unknown.t1.0', decision: 'denied' });
    });

    it('UX-4: an approval of an ordinary interaction is unchanged', async () => {
      const { service, resolve } = world({ role: 'owner' });
      await service.decide(command(ACTOR, 'approved'));
      expect(resolve.calledOnce).to.equal(true);
    });

    it('UX3 UE-4: the actor answers "it happened": journaled as answered, never re-called', async () => {
      const { service, resolve } = world({ role: 'owner' });
      const decided = await service.decide(unknown(ACTOR, 'answered'));
      expect(decided.interaction).to.deep.equal({ interactionId: 'ix.unknown.t1.0', decision: 'answered' });
      expect(resolve.firstCall.args[0]).to.include({ decision: 'answered', channel: 'web' });
    });

    it('UX3 UE-4: someone who may not deny may not answer either', async () => {
      const { service, resolve } = world({ role: 'viewer', audience: 'shared' });
      const refused = await service.decide(unknown('u-viewer', 'answered')).catch((error: unknown) => error);
      expect((refused as HarnessDecisionRefusedError).code).to.equal('NOT_ALLOWED_TO_DECIDE');
      expect(resolve.called).to.equal(false);
    });

    it('UX3 UE-5: "it happened" is no answer to an approval', async () => {
      const { service, resolve, warn } = world({ role: 'owner' });
      const refused = await service.decide(command(ACTOR, 'answered')).catch((error: unknown) => error);
      expect((refused as HarnessDecisionRefusedError).code).to.equal('DECISION_NOT_APPLICABLE');
      expect(resolve.called).to.equal(false);
      expect(warn.firstCall.args[1]).to.include({ reason: 'DECISION_NOT_APPLICABLE' });
    });

    it('UX3 UE-4: the route accepts answered, never with a scope', () => {
      const params = { agentKey: 'a1', sessionId: 's1', runId: 'r1', interactionId: 'ix.unknown.t1.0' };
      expect(decideSessionInteractionSchema.safeParse({ params, body: { decision: 'answered', idempotencyKey: 'k' } }).success).to.equal(true);
      expect(
        decideSessionInteractionSchema.safeParse({ params, body: { decision: 'answered', idempotencyKey: 'k', scope: 'conversation' } }).success,
      ).to.equal(false);
    });

    it('UX-1: the refusal reaches the client as 403 with its code', () => {
      const error = transportError(new HarnessDecisionRefusedError('OPERATOR_REQUIRED', 'no')) as {
        statusCode: number;
        code: string;
      };
      expect([error.statusCode, error.code]).to.deep.equal([403, 'HTTP_AGENT_HARNESS_OPERATOR_REQUIRED']);
    });
  });

  it('a refusal reaches the client as 403 with its code', () => {
    const error = transportError(new HarnessDecisionRefusedError('APPROVER_REQUIRED', 'no')) as {
      statusCode: number;
      code: string;
    };
    expect([error.statusCode, error.code]).to.deep.equal([403, 'HTTP_AGENT_HARNESS_APPROVER_REQUIRED']);
  });
});
