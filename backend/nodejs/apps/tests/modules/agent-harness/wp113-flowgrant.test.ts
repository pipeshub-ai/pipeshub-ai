/**
 * WP-113 FLOWGRANT (ADR-GRANT-01): "Allow for this conversation" reaches the whole conversation. Node names the
 * grant from the worker's own offer, writes a copy on every other open run of the conversation, and answers at once
 * every open ask the grant covers, as the person's own approval for the conversation. Without it, one recorded conversation asked
 * 14 times in one Claude Code child (one site three times).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import path from 'path';
import sinon from 'sinon';
import {
  ConversationGrantReach,
  GrantGiven,
} from '../../../src/modules/agent-harness/application/conversation-grant-reach';
import { SessionInteractionService } from '../../../src/modules/agent-harness/application/session-interactions';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import {
  ConversationGrant,
  coveredAsks,
  grantCovers,
  missingGrants,
  offeredGrant,
  runGrantState,
} from '../../../src/modules/agent-harness/domain/conversation-grants';
import { auditProjection } from '../../../src/modules/agent-harness/domain/audit';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { resolutionJournalEvents } from '../../../src/modules/agent-harness/domain/interactions';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { TimelineBuilder } from '../../../src/modules/agent-harness/projection/timeline';

const VECTOR = path.resolve(
  __dirname,
  '../../../../../../contracts/harness/vectors/flow-grants-recorded.json',
);
const RECORDED = JSON.parse(readFileSync(VECTOR, 'utf8')) as {
  events: Array<{
    seq: number;
    type: string;
    writer: string;
    data: Record<string, unknown>;
  }>;
};
const FETCH = 'web@searxng/fetch_url';
const ONYX =
  'sha256:4c5c0f51fdaf2b50038b8c674f654f5407e153408d5688d5b5fbcf38395aa0c3';
const PIPESHUB =
  'sha256:e50a24657b577a620ef914bb453c46e4e282f380a38c19026372cd82626c16cb';

const event = (
  runId: string,
  seq: number,
  type: string,
  writer: 'worker' | 'node',
  data: Record<string, unknown>,
): JournalEvent =>
  ({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: runId,
    runId,
    seq,
    type,
    writer,
    schemaVersion: 1,
    critical: false,
    visibility: 'owner',
    ts: '2026-11-07T12:32:30.000Z',
    data,
  }) as JournalEvent;

const offer = (
  ix: string,
  key: string,
  sensitivity = 'private',
  extra: Record<string, unknown> = {},
) => ({
  interactionId: ix,
  toolId: FETCH,
  rule: 'flow_external_write',
  layer: 'flow',
  conversationGrant: true,
  sensitivity,
  destination: { key, label: key === ONYX ? 'onyx.app' : 'pipeshub.com' },
  ...extra,
});

/** One open ask on a run's stream: the worker's reason and its request. */
const asking = (
  runId: string,
  seq: number,
  ix: string,
  key: string,
  sensitivity = 'private',
): JournalEvent[] => [
  event(
    runId,
    seq,
    'interaction.explained',
    'worker',
    offer(ix, key, sensitivity),
  ),
  event(runId, seq + 1, 'interaction.requested', 'worker', {
    interactionId: ix,
    kind: 'approval',
  }),
];

const run = (runId: string, status = 'running'): HarnessRun =>
  ({
    orgId: 'org-1',
    sessionId: 'ses-1',
    runId,
    harnessMajor: 2,
    status,
    // `p_owner`, the principal every `given` names: the conversation's runs act for the person who decides (D9).
    actorId: 'owner',
  }) as unknown as HarnessRun;

/** A conversation whose runs' journals the fakes read and append to, as Node's store does. */
function conversation(
  streams: Record<string, JournalEvent[]>,
  runs: HarnessRun[],
) {
  const appended: Array<{
    runId: string;
    events: Array<{ type: string; data: Record<string, unknown> }>;
  }> = [];
  const resolveAll = sinon
    .stub()
    .callsFake(async (command: { runId: string; interactionIds: string[] }) => {
      const stream = streams[command.runId] ?? [];
      for (const id of command.interactionIds) {
        const asked = runGrantState(stream).openOffers.get(
          id,
        ) as ConversationGrant;
        stream.push(
          event(
            command.runId,
            stream.length + 100,
            'interaction.resolved',
            'node',
            { interactionId: id, decision: 'approved' },
          ),
        );
        stream.push(
          event(
            command.runId,
            stream.length + 100,
            'interaction.granted',
            'node',
            { interactionId: id, scope: 'conversation', ...asked },
          ),
        );
      }
      return {};
    });
  const reach = new ConversationGrantReach<undefined>(
    {
      grantState: async (_org: string, runId: string) =>
        runGrantState(streams[runId] ?? []),
      openSessionRuns: async () =>
        runs.filter(
          (each) => !['completed', 'failed', 'cancelled'].includes(each.status),
        ),
    },
    { resolveAll } as never,
    {
      append: async (prepared: never) => {
        const batch = prepared as {
          runId: string;
          events: Array<{ type: string; data: Record<string, unknown> }>;
        };
        appended.push({ runId: batch.runId, events: batch.events });
        const stream = (streams[batch.runId] ??= []);
        for (const each of batch.events)
          stream.push(
            event(
              batch.runId,
              stream.length + 200,
              each.type,
              'node',
              each.data,
            ),
          );
        return { streamId: batch.runId, firstSeq: 1, lastSeq: 1, ts: '' };
      },
    },
    { prepare: async (append: never) => append as never },
    async (work) => work(undefined),
  );
  return { reach, appended, resolveAll };
}

const given = (runId: string, interactionIds: string[]): GrantGiven => ({
  orgId: 'org-1',
  sessionId: 'ses-1',
  runId,
  interactionIds,
  by: 'p_owner',
  channel: 'web',
  commandId: 'cmd-1',
});

describe('WP-113 FLOWGRANT: a conversation grant reaches the conversation', () => {
  it('FG-N1: the grant names what the worker offered, never what the client said', () => {
    const offers = new Map([
      [
        'ix.approve.k1',
        { toolId: FETCH, key: ONYX, sensitivity: 'private' as const },
      ],
    ]);
    const events = resolutionJournalEvents(
      { status: 'running' },
      {
        interactionId: 'ix.approve.k1',
        decision: 'approved',
        channel: 'web',
        scope: 'conversation',
      },
      'p_owner',
      offers,
    );
    expect(events.map((each) => each.type)).to.deep.equal([
      'interaction.resolved',
      'interaction.granted',
    ]);
    expect(events[1]?.data).to.deep.equal({
      interactionId: 'ix.approve.k1',
      scope: 'conversation',
      toolId: FETCH,
      key: ONYX,
      sensitivity: 'private',
      by: 'p_owner',
    });
    expect(
      auditProjection('interaction.granted', events[1]?.data ?? {})?.fields,
    ).to.include({ toolId: FETCH, sensitivity: 'private' });
  });

  it("FG-N2: no grant is offered for the org's own asks, and one covers only its tool, place and data", () => {
    expect(
      offeredGrant(offer('ix', ONYX, 'private', { rule: 'external_policy' })),
    ).to.equal(null);
    expect(
      offeredGrant(offer('ix', ONYX, 'private', { conversationGrant: false })),
    ).to.equal(null);
    const grant = offeredGrant(
      offer('ix', ONYX, 'private'),
    ) as ConversationGrant;
    expect(
      grantCovers(grant, { toolId: FETCH, key: ONYX, sensitivity: 'private' }),
    ).to.equal(true);
    expect(
      grantCovers(grant, { toolId: FETCH, key: ONYX, sensitivity: 'public' }),
    ).to.equal(true);
    expect(
      grantCovers(grant, {
        toolId: FETCH,
        key: PIPESHUB,
        sensitivity: 'private',
      }),
    ).to.equal(false);
    expect(
      grantCovers(grant, {
        toolId: 'web@searxng/web_search',
        key: ONYX,
        sensitivity: 'private',
      }),
    ).to.equal(false);
    const overPublic = { ...grant, sensitivity: 'public' as const };
    expect(
      grantCovers(overPublic, {
        toolId: FETCH,
        key: ONYX,
        sensitivity: 'private',
      }),
    ).to.equal(false);
    // Round 20 gate: an offer that names no data (before FLOWGRANT) holds at any data, as it did.
    const { sensitivity: _dropped, ...unnamed } = offer('ix', ONYX);
    _dropped;
    const legacy = offeredGrant(unnamed) as ConversationGrant;
    expect(legacy.sensitivity).to.equal('private');
    expect(grantCovers(legacy, { toolId: FETCH, key: ONYX, sensitivity: 'private' })).to.equal(true);
    expect(grantCovers(legacy, { toolId: FETCH, key: ONYX, sensitivity: 'unknown' })).to.equal(true);
  });

  it('FG-N3: a grant given in a child reaches its parent and sibling as a copy, never twice, never an ended run', async () => {
    const streams: Record<string, JournalEvent[]> = {
      child: [
        ...asking('child', 1, 'ix.approve.k1', ONYX),
        event('child', 3, 'interaction.resolved', 'node', {
          interactionId: 'ix.approve.k1',
          decision: 'approved',
        }),
        event('child', 4, 'interaction.granted', 'node', {
          interactionId: 'ix.approve.k1',
          scope: 'conversation',
          toolId: FETCH,
          key: ONYX,
          sensitivity: 'private',
        }),
      ],
      parent: [],
      sibling: [],
      ended: [],
    };
    const { reach, appended, resolveAll } = conversation(streams, [
      run('parent', 'waiting_approval'),
      run('child'),
      run('sibling'),
      run('ended', 'completed'),
    ]);
    const reached = await reach.reach(given('child', ['ix.approve.k1']));
    expect(reached).to.deep.equal({ shared: 2, answered: 0 });
    expect(appended.map((each) => each.runId).sort()).to.deep.equal([
      'parent',
      'sibling',
    ]);
    expect(appended[0]?.events[0]?.data).to.deep.equal({
      interactionId: 'ix.approve.k1',
      scope: 'conversation',
      toolId: FETCH,
      key: ONYX,
      sensitivity: 'private',
      fromRunId: 'child',
    });
    expect(resolveAll.called).to.equal(false);
    // A second grant for the same place brings nothing new: the copies are already there.
    const again = await reach.reach(given('child', ['ix.approve.k1']));
    expect(again.shared).to.equal(0);
  });

  it('FG-N4: every open ask the grant covers is answered at once, as the person, for the conversation', async () => {
    const streams: Record<string, JournalEvent[]> = {
      child: [
        ...asking('child', 1, 'ix.approve.k1', ONYX),
        ...asking('child', 3, 'ix.approve.k2', ONYX),
        ...asking('child', 5, 'ix.approve.k3', PIPESHUB),
        event('child', 7, 'interaction.resolved', 'node', {
          interactionId: 'ix.approve.k1',
          decision: 'approved',
        }),
        event('child', 8, 'interaction.granted', 'node', {
          interactionId: 'ix.approve.k1',
          scope: 'conversation',
          toolId: FETCH,
          key: ONYX,
          sensitivity: 'private',
        }),
      ],
      sibling: [
        ...asking('sibling', 1, 'ix.approve.t2.0', ONYX),
        ...asking('sibling', 3, 'ix.approve.t2.1', ONYX, 'public'),
      ],
    };
    const { reach, resolveAll } = conversation(streams, [
      run('child'),
      run('sibling'),
    ]);
    const reached = await reach.reach(given('child', ['ix.approve.k1']));
    expect(reached.answered).to.equal(3);
    const calls = resolveAll.getCalls().map((call) => call.args[0]);
    expect(calls.find((call) => call.runId === 'child')).to.deep.include({
      interactionIds: ['ix.approve.k2'],
      decision: 'approved',
      scope: 'conversation',
      by: 'p_owner',
      commandId: 'cmd-1:grant:child',
    });
    expect(
      calls.find((call) => call.runId === 'sibling')?.interactionIds,
    ).to.deep.equal(['ix.approve.t2.0', 'ix.approve.t2.1']);
    // Nothing about onyx.app is left waiting; pipeshub.com still asks.
    expect([
      ...runGrantState(streams['child'] ?? []).openOffers.keys(),
    ]).to.deep.equal(['ix.approve.k3']);
    expect(runGrantState(streams['sibling'] ?? []).openOffers.size).to.equal(0);
  });

  it('FG-N5: an ask decided meanwhile stands; a failing run never stops the others', async () => {
    const streams: Record<string, JournalEvent[]> = {
      child: [
        ...asking('child', 1, 'ix.approve.k1', ONYX),
        event('child', 3, 'interaction.resolved', 'node', {
          interactionId: 'ix.approve.k1',
          decision: 'approved',
        }),
        event('child', 4, 'interaction.granted', 'node', {
          interactionId: 'ix.approve.k1',
          scope: 'conversation',
          toolId: FETCH,
          key: ONYX,
          sensitivity: 'private',
        }),
      ],
      a: asking('a', 1, 'ix.approve.k5', ONYX),
      b: asking('b', 1, 'ix.approve.k6', ONYX),
    };
    const { reach, resolveAll } = conversation(streams, [
      run('a'),
      run('b'),
      run('child'),
    ]);
    resolveAll.callsFake(async (command: { runId: string }) => {
      if (command.runId === 'a')
        throw new HarnessConflictError('INTERACTION_NOT_OPEN', 'decided');
      if (command.runId === 'b') throw new Error('store down');
      return {};
    });
    const reached = await reach.reach(given('child', ['ix.approve.k1']));
    expect(reached.answered).to.equal(0);
    expect(reached.shared).to.equal(2);
  });

  it("FG-N6: a copy of another run's grant answers none of this run's cards, even with the same id", () => {
    const projector = new TimelineBuilder('sibling');
    const sibling = [
      event('sibling', 1, 'tool.intent', 'worker', {
        invocationId: 't2.0',
        toolId: FETCH,
      }),
      ...asking('sibling', 2, 'ix.approve.t2.0', PIPESHUB),
      event('sibling', 4, 'interaction.granted', 'node', {
        interactionId: 'ix.approve.t2.0',
        scope: 'conversation',
        toolId: FETCH,
        key: ONYX,
        sensitivity: 'private',
        fromRunId: 'child',
      }),
    ];
    for (const each of sibling) projector.apply(each);
    const card = projector.timeline.approvals.find(
      (each) => each.interactionId === 'ix.approve.t2.0',
    );
    expect(card?.status).to.equal('pending');
    expect(card?.scope).to.equal(undefined);
    expect(runGrantState(sibling).held).to.deep.equal([
      { toolId: FETCH, key: ONYX, sensitivity: 'private' },
    ]);
    expect(
      missingGrants(runGrantState(sibling).held, [
        { toolId: FETCH, key: ONYX, sensitivity: 'private' },
      ]),
    ).to.deep.equal([]);
  });

  it('FG-N7: asks that waited together share decidedBefore; an engine asking one at a time gets one each', () => {
    const projector = new TimelineBuilder('child');
    for (const each of RECORDED.events)
      projector.apply(
        event(
          'child',
          each.seq,
          each.type,
          each.writer as 'worker' | 'node',
          each.data,
        ),
      );
    const approvals = projector.timeline.approvals;
    expect(approvals).to.have.length(14);
    expect(new Set(approvals.map((each) => each.afterTurns)).size).to.equal(1);
    expect(approvals.map((each) => each.decidedBefore)).to.deep.equal([
      ...Array(14).keys(),
    ]);
  });

  it('FG-N8 replay of the recorded conversation: after one conversation grant for onyx.app, the 2nd and 3rd onyx.app calls are covered', () => {
    const explained = RECORDED.events.filter(
      (each) => each.type === 'interaction.explained',
    );
    const onyx = explained.filter(
      (each) =>
        (each.data['destination'] as { label: string }).label === 'onyx.app',
    );
    expect(onyx).to.have.length(3);
    const grant = offeredGrant(onyx[0]?.data ?? {}) as ConversationGrant;
    const later = new Map(
      onyx
        .slice(1)
        .map((each) => [
          String(each.data['interactionId']),
          offeredGrant(each.data) as ConversationGrant,
        ]),
    );
    expect(coveredAsks(later, [grant])).to.have.length(2);
    const others = new Map(
      explained
        .filter(
          (each) =>
            (each.data['destination'] as { label: string }).label !==
            'onyx.app',
        )
        .map((each) => [
          String(each.data['interactionId']),
          offeredGrant(each.data) as ConversationGrant,
        ]),
    );
    expect(coveredAsks(others, [grant])).to.deep.equal([]);
  });

  it('FG-N9: the session service takes a conversation approval to the conversation, and nothing else', async () => {
    const subject = {
      orgId: 'org-1',
      sessionId: 'ses-1',
      runId: 'child',
      actorId: 'u-actor',
      harnessMajor: 2,
      status: 'running',
    } as unknown as HarnessRun;
    const reach = sinon.stub().resolves({ shared: 1, answered: 0 });
    const service = new SessionInteractionService({
      executions: {
        get: sinon.stub().resolves(subject),
      } as never,
      interactions: {
        resolve: sinon.stub().resolves({ commandId: 'c', run: subject }),
      } as never,
      sessions: {
        findStreamAccess: sinon.stub().resolves({ audience: 'owner' }),
      } as never,
      roles: { roleOf: sinon.stub().resolves('owner') },
      approvers: new InMemoryApproverDirectory(new Map(), new Map()),
      grants: { reach },
    });
    const once = {
      orgId: 'org-1',
      actorId: 'u-actor',
      sessionId: 'ses-1',
      runId: 'child',
      interactionId: 'ix.approve.k1',
      decision: 'approved' as const,
      idempotencyKey: 'k-1',
      channel: 'web' as const,
    };
    await service.decide(once);
    expect(reach.called).to.equal(false);
    await service.decide({ ...once, scope: 'conversation' });
    expect(reach.firstCall.args[0]).to.deep.include({
      sessionId: 'ses-1',
      runId: 'child',
      by: 'p_u-actor',
    });
    expect(reach.firstCall.args[0].interactionIds).to.deep.equal([
      'ix.approve.k1',
    ]);
    reach.rejects(new Error('down'));
    const decided = await service.decide({
      ...once,
      idempotencyKey: 'k-2',
      scope: 'conversation',
    });
    expect(decided.interaction.decision).to.equal('approved');
  });
});
