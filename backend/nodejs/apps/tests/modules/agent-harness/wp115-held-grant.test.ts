/**
 * WP115-FU-2 (ADR-GRANT-01 D2): Node, the grants' authority, answers an ask a worker raises that a conversation
 * grant its run already holds covers, loop and engine alike. The race: Node wrote the grant's copy on the run after
 * the worker last read its stream and before the worker committed the ask, so the worker could not know and Node's
 * reach had read the run before the ask existed. The ask is resolved as the grant's person's approval for the
 * conversation, through the usual decision path; it is never left waiting for anyone. When Node cannot tell, the ask
 * stays with the person.
 */
import { expect } from 'chai';
import { ConversationGrantReach } from '../../../src/modules/agent-harness/application/conversation-grant-reach';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import {
  grantedEvent,
  runGrantState,
  type ConversationGrant,
} from '../../../src/modules/agent-harness/domain/conversation-grants';
import { HarnessRun, journalStreamId } from '../../../src/modules/agent-harness/domain/execution';
import { principalRefFor } from '../../../src/modules/agent-harness/domain/run-spec';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { TimelineBuilder } from '../../../src/modules/agent-harness/projection/timeline';
import { contractClock, createContractHarness, DURABLE_CAPABILITIES } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { workerEvent } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';

const RUN_ID = 'run-held';
const FETCH = 'web@searxng/fetch_url';
const ONYX = `sha256:${'4'.repeat(64)}`;
const OTHER = `sha256:${'5'.repeat(64)}`;
const SIBLING_ACTOR = 'sibling_person';
const SIBLING = `p_${SIBLING_ACTOR}`;

const explained = (interactionId: string, key = ONYX, sensitivity = 'private', conversationGrant = true) =>
  workerEvent(
    'interaction.explained',
    {
      interactionId,
      rule: 'flow_external_write',
      layer: 'flow',
      toolId: FETCH,
      sensitivity,
      sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
      moreSources: 0,
      destination: { label: key === ONYX ? 'onyx.app' : 'other.example', key },
      conversationGrant,
    },
    { critical: false },
  );
const requested = (interactionId: string) =>
  workerEvent('interaction.requested', {
    interactionId,
    kind: 'approval',
    bindingDigest: `sha256:${'1'.repeat(64)}`,
  });

async function world(options: { failing?: boolean } = {}) {
  const clock = contractClock();
  const backend = inMemoryBackend(clock);
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const journals = new JournalService(backend.payloads, testCipher());
  const service = new ExecutionService({
    store: backend.executionStore,
    journal: backend.journal,
    journals,
    capabilities: DURABLE_CAPABILITIES,
  });
  const interactions = new InteractionService(service);
  const reach = new ConversationGrantReach<undefined>(
    options.failing === true
      ? {
          grantState: async () => {
            throw new Error('journal unavailable');
          },
          openSessionRuns: async () => [],
        }
      : service,
    interactions,
    backend.journal as never,
    journals,
    async (work) => work(undefined),
  );
  /** What each listener was told, in order: the held answer, then the run's wait. */
  const told: string[] = [];
  // As the composition wires it (`binders/session-views.ts`).
  service.onGrantableAsks(async (run, ids, commandId) => {
    const answered = await reach.answerHeld(run, ids, commandId);
    told.push(`held:${answered}`);
    return answered;
  });
  service.onRunWaiting(() => told.push('waiting'));
  const admitted = await h.admit(
    { input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } },
    RUN_ID,
  );
  const orgId = admitted.run.orgId;
  const claimed = (await service.claim({ orgId, runId: RUN_ID, workerId: 'w', attemptId: 'a1', leaseDurationMs: 60_000 }))
    .run;
  let commits = 0;
  const commit = (events: ReturnType<typeof workerEvent>[], suspendOn?: string) =>
    service.commit({
      commandId: `ask-${(commits += 1)}`,
      orgId,
      runId: RUN_ID,
      workerId: 'w',
      attemptId: 'a1',
      fence: claimed.fence,
      events,
      legacyEvents: [],
      outbox: [],
      ...(suspendOn !== undefined
        ? {
            transition: {
              status: 'waiting_approval' as const,
              detail: { kind: 'suspend' as const, reason: 'approval' as const, interactionId: suspendOn },
            },
          }
        : {}),
    });
  /** Node's copy of a grant a person gave in another run of the conversation (`ConversationGrantReach.share`). */
  const copyGrant = async (grant: ConversationGrant) => {
    const prepared = await journals.prepare({
      orgId,
      sessionId: claimed.sessionId,
      streamId: journalStreamId(claimed),
      runId: RUN_ID,
      writer: 'node',
      events: [grantedEvent('ix.approve.sibling', grant, 'run-sibling')],
    });
    await backend.journal.append(prepared);
  };
  const journal = async (): Promise<JournalEvent[]> =>
    journals.resolve(await backend.journal.readRun(orgId, RUN_ID, 0, 1_000));
  const run = async (): Promise<HarnessRun> => (await backend.executionStore.get(orgId, RUN_ID))!;
  return { commit, copyGrant, journal, run, told, actor: admitted.run.actorId };
}

const resolvedOf = (events: JournalEvent[], ix: string) =>
  events.find((e) => e.type === 'interaction.resolved' && e.data?.['interactionId'] === ix);

describe('WP115-FU-2: Node answers an ask a grant its run already holds covers', () => {
  it('HG-1: a grant copied between the worker\'s last read and its ask commit answers the ask: nobody is asked', async () => {
    const w = await world();
    // The same person's grant from another of their runs of the conversation (D9: never someone else's).
    await w.copyGrant({ toolId: FETCH, key: ONYX, sensitivity: 'private', by: principalRefFor(w.actor) });
    await w.commit([explained('ix.approve.t2.0'), requested('ix.approve.t2.0')]);

    const events = await w.journal();
    const resolved = resolvedOf(events, 'ix.approve.t2.0');
    expect(resolved?.writer).to.equal('node');
    expect(resolved?.data).to.include({ decision: 'approved', by: principalRefFor(w.actor), channel: 'system' });
    const granted = events.filter((e) => e.type === 'interaction.granted' && e.data?.['interactionId'] === 'ix.approve.t2.0');
    expect(granted.map((e) => e.data?.['scope'])).to.deep.equal(['conversation']);
    expect((await w.run()).openInteractions ?? []).to.not.include('ix.approve.t2.0');
    expect(runGrantState(events).openOffers.size).to.equal(0);
    // The chat's view: the ask is a receipt "Allowed onyx.app for this conversation by you", not a waiting card.
    const timeline = new TimelineBuilder(RUN_ID);
    for (const event of events) timeline.apply(event);
    const [approval] = timeline.timeline.approvals;
    expect(approval).to.deep.include({ status: 'approved', scope: 'conversation' });
  });

  it('HG-2: a grant for other data, another place or a grant-less ask leaves the ask with the person', async () => {
    const w = await world();
    await w.copyGrant({ toolId: FETCH, key: ONYX, sensitivity: 'public', by: SIBLING });
    await w.commit([explained('ix.approve.t2.0'), requested('ix.approve.t2.0')]);
    await w.commit([explained('ix.approve.t3.0', OTHER, 'public'), requested('ix.approve.t3.0')]);
    await w.commit([explained('ix.approve.t4.0', ONYX, 'public', false), requested('ix.approve.t4.0')]);
    const events = await w.journal();
    expect(resolvedOf(events, 'ix.approve.t2.0')).to.equal(undefined);
    expect(resolvedOf(events, 'ix.approve.t3.0')).to.equal(undefined);
    expect(resolvedOf(events, 'ix.approve.t4.0')).to.equal(undefined);
    expect((await w.run()).openInteractions).to.include.members(['ix.approve.t2.0', 'ix.approve.t3.0', 'ix.approve.t4.0']);
  });

  it('HG-3: the same or less sensitive data is covered; an older grant that names nobody is the run\'s actor\'s', async () => {
    const w = await world();
    await w.copyGrant({ toolId: FETCH, key: ONYX, sensitivity: 'private' });
    await w.commit([explained('ix.approve.t2.0', ONYX, 'public'), requested('ix.approve.t2.0')]);
    const resolved = resolvedOf(await w.journal(), 'ix.approve.t2.0');
    expect(resolved?.data).to.include({ decision: 'approved', by: principalRefFor(w.actor) });
  });

  it('HG-4: when Node cannot read the grants, the ask stays open and the commit still lands', async () => {
    const w = await world({ failing: true });
    await w.copyGrant({ toolId: FETCH, key: ONYX, sensitivity: 'private', by: SIBLING });
    const receipt = await w.commit([explained('ix.approve.t2.0'), requested('ix.approve.t2.0')]);
    expect(receipt.run.status).to.equal('running');
    expect(resolvedOf(await w.journal(), 'ix.approve.t2.0')).to.equal(undefined);
    expect((await w.run()).openInteractions).to.include('ix.approve.t2.0');
  });

  it('HG-7: a run that suspends on an ask Node answers is told waiting only after the answer, so no approver is told of it', async () => {
    const w = await world();
    await w.copyGrant({ toolId: FETCH, key: ONYX, sensitivity: 'private', by: principalRefFor(w.actor) });
    await w.commit([explained('ix.approve.t2.0'), requested('ix.approve.t2.0')], 'ix.approve.t2.0');
    expect(w.told).to.deep.equal(['held:1', 'waiting']);
    expect((await w.run()).openInteractions ?? []).to.deep.equal([]);
  });

  it('HG-5: the grant Node writes names who gave it, so a later ask it covers is theirs', () => {
    const events = [
      { type: 'interaction.explained', writer: 'worker', data: { ...explained('ix-a').data } },
      { type: 'interaction.requested', writer: 'worker', data: { interactionId: 'ix-a' } },
      { type: 'interaction.resolved', writer: 'node', data: { interactionId: 'ix-a', decision: 'approved', by: 'p_owner' } },
      { type: 'interaction.granted', writer: 'node', data: { interactionId: 'ix-a', scope: 'conversation', toolId: FETCH, key: ONYX, sensitivity: 'private' } },
    ] as unknown as JournalEvent[];
    expect(runGrantState(events).held).to.deep.equal([{ toolId: FETCH, key: ONYX, sensitivity: 'private', by: 'p_owner' }]);
  });

  it('HG-6: the reach reads a run again after copying the grant, so an ask raised meanwhile is answered', async () => {
    const asked = { toolId: FETCH, key: ONYX, sensitivity: 'private' as const };
    let reads = 0;
    const resolved: string[][] = [];
    const reach = new ConversationGrantReach<undefined>(
      {
        grantState: async (_org: string, runId: string) => {
          if (runId === 'run-given') {
            return {
              held: [],
              openOffers: new Map(),
              given: new Map([['ix-given', { ...asked, by: SIBLING }]]),
              declined: [],
              openAsks: new Map(),
              declinedHere: new Map(),
            };
          }
          reads += 1;
          // The worker's ask lands while Node writes the copy: the first read misses it.
          return {
            held: reads > 1 ? [{ ...asked, by: SIBLING }] : [],
            openOffers: reads > 1 ? new Map([['ix-late', asked]]) : new Map(),
            given: new Map(),
            declined: [],
            openAsks: new Map(),
            declinedHere: new Map(),
          };
        },
        openSessionRuns: async () =>
          [
            { orgId: 'org-1', sessionId: 'ses-1', runId: 'run-given', status: 'running', actorId: SIBLING_ACTOR },
            { orgId: 'org-1', sessionId: 'ses-1', runId: 'run-other', status: 'running', actorId: SIBLING_ACTOR },
          ] as unknown as HarnessRun[],
      },
      {
        resolveAll: async (command: { interactionIds: readonly string[] }) => {
          resolved.push([...command.interactionIds]);
          return {} as never;
        },
      },
      { append: async () => ({ streamId: 's', firstSeq: 1, lastSeq: 1, ts: '' }) } as never,
      { prepare: async (append: never) => append as never },
      async (work) => work(undefined),
    );
    const reached = await reach.reach({
      orgId: 'org-1',
      sessionId: 'ses-1',
      runId: 'run-given',
      interactionIds: ['ix-given'],
      by: SIBLING,
      channel: 'web',
      commandId: 'cmd-1',
    });
    expect(reached).to.deep.equal({ shared: 1, answered: 1 });
    expect(resolved).to.deep.equal([['ix-late']]);
  });
});
