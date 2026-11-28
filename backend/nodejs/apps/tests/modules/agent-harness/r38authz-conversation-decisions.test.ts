/**
 * R38-AUTHZ (ADR-GRANT-01 D9): a person's conversation decision, an "Allow for this conversation" or a decline with
 * its reason, holds for that person's runs of the conversation and nobody else's.
 *
 * - SEC-B-01, SEC-C-04: a collaborator's grant never answers, nor is copied onto, the owner's runs (and back).
 * - R36LIVE-FU-1: a coding job's call the person declined with a reason is declined again, with the reason, when the
 *   parent (or a later turn) makes the same call; the person is not asked twice.
 *
 * The world is Node's own: the in-memory execution store, real `ExecutionService`, `InteractionService` and
 * `ConversationGrantReach`, as `binders/session-views.ts` wires them, and a real child spawned for the parent.
 */
import { expect } from 'chai';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { ConversationGrantReach } from '../../../src/modules/agent-harness/application/conversation-grant-reach';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import {
  conversationGrantList,
  declinableAsk,
  grantRevokedEvent,
  runGrantState,
  sessionDecisions,
} from '../../../src/modules/agent-harness/domain/conversation-grants';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { principalRefFor } from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { workerEvent } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';

const TICKET = 'fake-saas@team/create_ticket';
const ARGS = `sha256:${'c'.repeat(64)}`;
const OTHER_ARGS = `sha256:${'d'.repeat(64)}`;
const FETCH = 'web@searxng/fetch_url';
const EVIL = `sha256:${'e'.repeat(64)}`;
const REASON = 'Not in this tracker: we keep these on the roadmap board';

/** A write a layer's rule holds (no grant offered), naming its arguments: what the Codex job's `create_ticket` asked. */
const ticketAsk = (interactionId: string, argsDigest = ARGS) => [
  workerEvent(
    'interaction.explained',
    { interactionId, rule: 'writes', layer: 'org', toolId: TICKET, conversationGrant: false, argsDigest },
    { critical: false },
  ),
  workerEvent('interaction.requested', { interactionId, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
];

/** A fetch to attacker.example after a private read: the flow rule asks and offers a grant (SEC-B-01). */
const fetchAsk = (interactionId: string) => [
  workerEvent(
    'interaction.explained',
    {
      interactionId,
      rule: 'flow_external_write',
      layer: 'flow',
      toolId: FETCH,
      sensitivity: 'private',
      sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
      moreSources: 0,
      destination: { label: 'attacker.example', key: EVIL },
      conversationGrant: true,
    },
    { critical: false },
  ),
  workerEvent('interaction.requested', { interactionId, kind: 'approval', bindingDigest: `sha256:${'2'.repeat(64)}` }),
];

interface Claimed {
  run: HarnessRun;
  workerId: string;
  attemptId: string;
}

async function world() {
  const clock = contractClock();
  const backend = inMemoryBackend(clock, { budgets: new InMemoryBudgetLedger(() => clock.now()) });
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
  h.setAgentDefinition((agentKey) => agentDefinition({ agentKey, maxChildren: 2 }));
  const journals = new JournalService(backend.payloads, testCipher());
  const service = h.service;
  const interactions = new InteractionService(service);
  const reach = new ConversationGrantReach<undefined>(
    service,
    interactions,
    backend.journal as never,
    journals,
    async (work) => work(undefined),
  );
  service.onGrantableAsks((run, ids, commandId) => reach.answerHeld(run, ids, commandId));

  const claim = async (run: HarnessRun, workerId: string): Promise<Claimed> => ({
    run: await h.claim(run, workerId, 60_000),
    workerId,
    attemptId: `attempt-${workerId}`,
  });
  // Alice owns the conversation; Bob is a collaborator in it (OWD-19): each run acts for its sender.
  const alice = await claim((await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' })).run, 'w-alice');
  const sessionId = alice.run.sessionId;
  const bobId = `${h.tenant.actorId.slice(0, 20)}b0b0`;
  const children = new ChildRunService({ runs: backend.executionStore, children: backend.childRuns!, journals });
  const spawned = await children.spawn(
    { orgId: alice.run.orgId, runId: alice.run.runId, workerId: alice.workerId, attemptId: alice.attemptId, fence: alice.run.fence },
    { invocationId: 't1.0', goal: 'File the PAY-1423 ticket', grants: {} },
  );
  const job = await claim((await service.get(alice.run.orgId, spawned.childRunId))!, 'w-job');

  let commits = 0;
  const ask = (who: Claimed, events: ReturnType<typeof workerEvent>[]) =>
    service.commit({
      commandId: `ask-${(commits += 1)}`,
      orgId: who.run.orgId,
      runId: who.run.runId,
      workerId: who.workerId,
      attemptId: who.attemptId,
      fence: who.run.fence,
      events,
      legacyEvents: [],
      outbox: [],
    });
  /** A person's decision as `SessionInteractionService.decideIn` makes it: resolved, then reached. */
  const decide = async (
    who: Claimed,
    interactionId: string,
    by: string,
    decision: 'approved' | 'denied',
    options: { reason?: string; scope?: 'conversation' } = {},
  ) => {
    const commandId = `decide-${(commits += 1)}`;
    await interactions.resolve({
      orgId: who.run.orgId,
      runId: who.run.runId,
      interactionId,
      decision,
      channel: 'web',
      by,
      commandId,
      ...(options.reason !== undefined ? { reason: options.reason } : {}),
      ...(options.scope !== undefined ? { scope: options.scope } : {}),
    });
    if (decision === 'denied' || options.scope === 'conversation') {
      await reach.reach({
        decision,
        orgId: who.run.orgId,
        sessionId: who.run.sessionId,
        runId: who.run.runId,
        interactionIds: [interactionId],
        by,
        channel: 'web',
        commandId,
      });
    }
  };
  const journal = async (who: Claimed): Promise<JournalEvent[]> =>
    journals.resolve(await backend.journal.readRun(who.run.orgId, who.run.runId, 0, 1_000));
  const resolvedOf = async (who: Claimed, interactionId: string) =>
    (await journal(who)).find((e) => e.type === 'interaction.resolved' && e.data?.['interactionId'] === interactionId);
  const open = async (who: Claimed): Promise<string[]> =>
    (await backend.executionStore.get(who.run.orgId, who.run.runId))?.openInteractions ?? [];
  const reasonOf = (event: JournalEvent | undefined): string | undefined => {
    const ref = event?.data?.['answerRef'] as { ref?: string } | undefined;
    const inline = ref?.ref?.startsWith('inline:') === true ? ref.ref.slice('inline:'.length) : undefined;
    return inline !== undefined ? Buffer.from(inline, 'base64').toString('utf8') : undefined;
  };
  /**
   * The conversation's next turn, once the open one ended (a session runs one turn at a time; its jobs run on): by
   * Bob, or by Alice again. The contract harness wires no session roles (collaborator admission is
   * collaborator-turns.test.ts's), and the decisions read only the run's actor, so Bob's turn acts for him.
   */
  let turn = alice;
  const nextTurn = async (actorId: string, workerId: string): Promise<Claimed> => {
    // As the turn's own worker: the store fails no run another attempt still holds.
    await backend.executionStore.failRun(turn.run.orgId, turn.run.runId, 'ended', [], turn.attemptId);
    const admitted = (await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', sessionId })).run;
    const runs = (backend.executionStore as unknown as { runs: Map<string, HarnessRun> }).runs;
    for (const stored of runs.values()) {
      if (stored.runId === admitted.runId) stored.actorId = actorId;
    }
    turn = await claim((await service.get(admitted.orgId, admitted.runId))!, workerId);
    return turn;
  };
  return {
    h, backend, service, reach, alice, job, sessionId, ask, decide, journal, resolvedOf, open, reasonOf,
    bobTurn: () => nextTurn(bobId, 'w-bob'),
    aliceTurn: () => nextTurn(alice.run.actorId, 'w-alice-2'),
    pAlice: principalRefFor(alice.run.actorId),
    pBob: principalRefFor(bobId),
  };
}

describe('R38-AUTHZ: a conversation decision holds only for the runs of the person who made it', () => {
  it('D9-1 (R36LIVE-FU-1 repro): a job declined with a reason, the parent making the same call is declined again with it, not asked', async () => {
    const w = await world();
    expect(w.job.run.actorId).to.equal(w.alice.run.actorId);
    await w.ask(w.job, ticketAsk('ix.approve.t1.0'));
    await w.decide(w.job, 'ix.approve.t1.0', w.pAlice, 'denied', { reason: REASON });
    const own = (await w.journal(w.job)).find((e) => e.type === 'interaction.declined');
    expect(own?.data).to.deep.include({ toolId: TICKET, key: ARGS, by: w.pAlice, reason: REASON, scope: 'conversation' });
    // The parent was told: a copy names where it was declined.
    const copy = (await w.journal(w.alice)).find((e) => e.type === 'interaction.declined');
    expect(copy?.data).to.deep.include({ toolId: TICKET, key: ARGS, by: w.pAlice, fromRunId: w.job.run.runId });

    // The parent loop then calls the same tool with the same arguments.
    await w.ask(w.alice, ticketAsk('ix.approve.t3.0'));
    const answered = await w.resolvedOf(w.alice, 'ix.approve.t3.0');
    expect(answered?.data).to.deep.include({ decision: 'denied', by: w.pAlice, channel: 'system' });
    expect(w.reasonOf(answered)).to.equal(REASON);
    expect(await w.open(w.alice)).to.not.include('ix.approve.t3.0');
  });

  it('D9-2: the same tool with other arguments still asks; a decline never widens to another call', async () => {
    const w = await world();
    await w.ask(w.job, ticketAsk('ix.approve.t1.0'));
    await w.decide(w.job, 'ix.approve.t1.0', w.pAlice, 'denied', { reason: REASON });
    await w.ask(w.alice, ticketAsk('ix.approve.t3.0', OTHER_ARGS));
    expect(await w.resolvedOf(w.alice, 'ix.approve.t3.0')).to.equal(undefined);
    expect(await w.open(w.alice)).to.include('ix.approve.t3.0');
  });

  it("D9-3: Alice's decline never answers Bob's run: in a shared chat each person decides for their own runs", async () => {
    const w = await world();
    await w.ask(w.job, ticketAsk('ix.approve.t1.0'));
    await w.decide(w.job, 'ix.approve.t1.0', w.pAlice, 'denied', { reason: REASON });
    // Bob's turn reads the shared root stream, where Alice's decline sits.
    const bob = await w.bobTurn();
    await w.ask(bob, ticketAsk('ix.approve.b1.0'));
    expect(await w.resolvedOf(bob, 'ix.approve.b1.0')).to.equal(undefined);
    expect(await w.open(bob)).to.include('ix.approve.b1.0');
    // Alice's own job, still open, is declined the same call again.
    await w.ask(w.job, ticketAsk('ix.approve.t2.0'));
    expect((await w.resolvedOf(w.job, 'ix.approve.t2.0'))?.data).to.deep.include({ decision: 'denied', by: w.pAlice });
  });

  it("D9-4 (SEC-B-01, SEC-C-04): Bob's \"allow for this conversation\" neither answers nor reaches Alice's runs", async () => {
    const w = await world();
    await w.ask(w.job, fetchAsk('ix.approve.j1.0'));
    const bob = await w.bobTurn();
    await w.ask(bob, fetchAsk('ix.approve.b1.0'));
    await w.decide(bob, 'ix.approve.b1.0', w.pBob, 'approved', { scope: 'conversation' });
    expect(await w.resolvedOf(w.job, 'ix.approve.j1.0')).to.equal(undefined);
    expect((await w.journal(w.job)).some((e) => e.type === 'interaction.granted')).to.equal(false);
    await w.ask(w.job, fetchAsk('ix.approve.j2.0'));
    expect(await w.open(w.job)).to.include.members(['ix.approve.j1.0', 'ix.approve.j2.0']);
    // Alice's next turn reads the shared root stream, where Bob's grant sits: she is still asked.
    const alice = await w.aliceTurn();
    await w.ask(alice, fetchAsk('ix.approve.a1.0'));
    expect(await w.open(alice)).to.include('ix.approve.a1.0');
    const state = await w.service.grantState(alice.run.orgId, alice.run.runId, { session: true });
    expect(state.held).to.deep.equal([]);
  });

  it("D9-5: Alice's own grant still reaches her job and her later asks, as ADR-GRANT-01 D2 always said", async () => {
    const w = await world();
    await w.ask(w.job, fetchAsk('ix.approve.j1.0'));
    await w.ask(w.alice, fetchAsk('ix.approve.a1.0'));
    await w.decide(w.alice, 'ix.approve.a1.0', w.pAlice, 'approved', { scope: 'conversation' });
    expect((await w.resolvedOf(w.job, 'ix.approve.j1.0'))?.data).to.deep.include({ decision: 'approved', by: w.pAlice });
    await w.ask(w.alice, fetchAsk('ix.approve.a2.0'));
    expect((await w.resolvedOf(w.alice, 'ix.approve.a2.0'))?.data).to.deep.include({ decision: 'approved', by: w.pAlice });
    const next = await w.aliceTurn();
    await w.ask(next, fetchAsk('ix.approve.a9.0'));
    expect((await w.resolvedOf(next, 'ix.approve.a9.0'))?.data).to.deep.include({ decision: 'approved', by: w.pAlice });
  });

  it("D9-6: anyone but the run's person answering for the conversation is refused; their denial covers only that ask", async () => {
    const w = await world();
    const bob = await w.bobTurn();
    await w.ask(bob, fetchAsk('ix.approve.b1.0'));
    let refused: unknown;
    try {
      await w.decide(bob, 'ix.approve.b1.0', w.pAlice, 'approved', { scope: 'conversation' });
    } catch (error) {
      refused = error;
    }
    expect((refused as { code?: string }).code).to.equal('CONVERSATION_GRANT_NOT_OFFERED');
    expect(await w.open(bob)).to.include('ix.approve.b1.0');
    // The owner may deny a collaborator's call (AP-1's owner rule), but that is one answer, never Bob's decline.
    await w.ask(bob, ticketAsk('ix.approve.b2.0'));
    await w.decide(bob, 'ix.approve.b2.0', w.pAlice, 'denied', { reason: REASON });
    expect((await w.resolvedOf(bob, 'ix.approve.b2.0'))?.data).to.deep.include({ decision: 'denied', by: w.pAlice });
    expect((await w.journal(bob)).some((e) => e.type === 'interaction.declined')).to.equal(false);
    await w.ask(bob, ticketAsk('ix.approve.b3.0'));
    expect(await w.open(bob)).to.include('ix.approve.b3.0');
  });

  it('D9-7: a later turn of the same person is declined from the root stream, with the reason', async () => {
    const w = await world();
    await w.ask(w.alice, ticketAsk('ix.approve.t1.0'));
    await w.decide(w.alice, 'ix.approve.t1.0', w.pAlice, 'denied', { reason: REASON });
    const next = await w.aliceTurn();
    await w.ask(next, ticketAsk('ix.approve.t9.0'));
    const answered = await w.resolvedOf(next, 'ix.approve.t9.0');
    expect(answered?.data).to.deep.include({ decision: 'denied', by: w.pAlice });
    expect(w.reasonOf(answered)).to.equal(REASON);
  });
});

describe('R38-AUTHZ: the decision folds an attacker would try to bend', () => {
  const event = (
    runId: string,
    seq: number,
    type: string,
    writer: 'worker' | 'node',
    data: Record<string, unknown>,
  ): JournalEvent =>
    ({
      orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', runId, seq, type, writer,
      schemaVersion: 1, critical: false, visibility: 'owner', ts: `2026-11-13T12:00:${String(seq).padStart(2, '0')}.000Z`, data,
    }) as JournalEvent;
  const offer = (runId: string, seq: number, ix: string) => [
    event(runId, seq, 'interaction.explained', 'worker', {
      interactionId: ix, rule: 'flow_external_write', toolId: FETCH, conversationGrant: true, sensitivity: 'private',
      destination: { key: EVIL, label: 'attacker.example' },
    }),
    event(runId, seq + 1, 'interaction.requested', 'worker', { interactionId: ix, kind: 'approval' }),
  ];
  const grant = (runId: string, seq: number, ix: string, by: unknown, extra: Record<string, unknown> = {}) =>
    event(runId, seq, 'interaction.granted', 'node', {
      interactionId: ix, scope: 'conversation', toolId: FETCH, key: EVIL, sensitivity: 'private', by, ...extra,
    });

  it("only the run's own person's grants are held, whatever look-alike `by` a copy names", () => {
    for (const by of ['p_bob', 'P_alice', 'p_alice ', ' p_alice', 'p_alice​', 'p_аlice', 'p_alice/../bob']) {
      const state = runGrantState([...offer('run-a', 1, 'ix.1'), grant('run-a', 3, 'ix.other', by, { fromRunId: 'run-b' })], 'p_alice');
      expect(state.held, by).to.deep.equal([]);
    }
    const own = runGrantState([...offer('run-a', 1, 'ix.1'), grant('run-a', 3, 'ix.other', 'p_alice', { fromRunId: 'run-b' })], 'p_alice');
    expect(own.held).to.have.length(1);
  });

  it("a paired grant decided by someone else on a shared stream never holds; one naming nobody is the run's own", () => {
    const shared = [
      ...offer('run-bob', 1, 'ix.1'),
      event('run-bob', 3, 'interaction.resolved', 'node', { interactionId: 'ix.1', decision: 'approved', by: 'p_bob' }),
      event('run-bob', 4, 'interaction.granted', 'node', { interactionId: 'ix.1', scope: 'conversation' }),
    ];
    expect(runGrantState(shared, 'p_alice').held).to.deep.equal([]);
    expect(runGrantState(shared, 'p_bob').held).to.have.length(1);
    const legacy = [...offer('run-a', 1, 'ix.1'), event('run-a', 3, 'interaction.granted', 'node', { interactionId: 'ix.1', scope: 'conversation' })];
    expect(runGrantState(legacy, 'p_alice').held).to.have.length(1);
  });

  it('the root stream lends a later run only decisions that name their person and call', () => {
    const stream = [
      grant('run-1', 1, 'ix.1', 'p_bob'),
      event('run-1', 2, 'interaction.granted', 'node', { interactionId: 'ix.2', scope: 'conversation' }),
      event('run-1', 3, 'interaction.declined', 'node', { interactionId: 'ix.3', scope: 'conversation', toolId: TICKET, key: ARGS }),
      event('run-1', 4, 'interaction.declined', 'worker', { interactionId: 'ix.4', scope: 'conversation', toolId: TICKET, key: ARGS, by: 'p_alice' }),
    ];
    expect(sessionDecisions(stream, 'p_alice')).to.deep.equal({ held: [], declined: [] });
  });

  it("the person's latest decision for a call stands; the owner's revocation takes back a decline too", () => {
    const declined = event('run-a', 5, 'interaction.declined', 'node', {
      interactionId: 'ix.9', scope: 'conversation', toolId: FETCH, key: EVIL, by: 'p_alice', reason: REASON,
    });
    const after = runGrantState([grant('run-a', 1, 'ix.1', 'p_alice', { fromRunId: 'run-b' }), declined], 'p_alice');
    expect(after.held).to.deep.equal([]);
    expect(after.declined).to.have.length(1);
    const revoked = { ...event('run-a', 6, 'interaction.grant_revoked', 'node', {}), data: grantRevokedEvent({ toolId: FETCH, key: EVIL }, 'p_alice').data };
    expect(runGrantState([declined, revoked], 'p_alice').declined).to.deep.equal([]);
    const listed = conversationGrantList([...offer('run-a', 1, 'ix.9'), declined]);
    expect(listed.map((each) => [each.decision, each.by, each.destination])).to.deep.equal([['declined', 'p_alice', 'attacker.example']]);
  });

  it('an ask names its call by destination first, then its arguments; neither, and no decline can cover it', () => {
    expect(declinableAsk({ toolId: TICKET, argsDigest: ARGS })).to.deep.equal({ toolId: TICKET, key: ARGS });
    expect(declinableAsk({ toolId: FETCH, argsDigest: ARGS, destination: { key: EVIL } })).to.deep.equal({ toolId: FETCH, key: EVIL });
    expect(declinableAsk({ toolId: TICKET })).to.equal(null);
    expect(declinableAsk({ toolId: TICKET, argsDigest: '' })).to.equal(null);
    expect(declinableAsk({ argsDigest: ARGS })).to.equal(null);
  });
});
