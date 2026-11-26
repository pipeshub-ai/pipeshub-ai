/**
 * WP-64 (G-12 x G-13): a run that waits for its owner to reconnect a toolset (its vendor rejected the saved sign-in
 * during the run) is listed in the owner's inbox, and a link to it shows the same row, naming the service and when
 * it rejected the sign-in. Nobody else is asked to connect someone else's account; the timeline and the chat's
 * connect part carry why it waits.
 */
import { expect } from 'chai';
import { ApprovalQueueService } from '../../../src/modules/agent-harness/application/approval-queue';
import { pendingApprovalsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/pending-approvals';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { connectPart } from '../../../src/modules/agent-harness/projection/transcript';

const ORG = 'org-1';
const WAIT = 'ix.connect.t2.0';
const AT = '2026-11-02T00:29:19.000Z';

let seq = 0;
const event = (type: string, data: Record<string, unknown>, writer = 'worker'): JournalEvent =>
  ({
    orgId: ORG, sessionId: 's-1', streamId: 's-1', seq: (seq += 1), runId: 'r-1', attemptId: 'r-1.a1', fence: 1,
    type, schemaVersion: 1, critical: !type.startsWith('x.'), ts: AT, writer, visibility: 'owner', data,
  }) as JournalEvent;

const JOURNAL: JournalEvent[] = [
  event('turn.started', { turn: 2 }),
  event('model.response', {
    messageRef: inlineContentRef(canonicalJsonBytes({ text: '', toolCalls: [{ id: 'c2', name: 'github_list_issues', args: {} }] })),
    toolCalls: [{ callId: 'c2', modelName: 'github_list_issues', argsDigest: `sha256:${'c'.repeat(64)}` }],
    usage: { inputTokens: 0, outputTokens: 0 },
    provider: 'p',
    transport: 't',
  }),
  event('interaction.requested', {
    interactionId: WAIT,
    kind: 'connect',
    bindingDigest: `sha256:${'a'.repeat(64)}`,
    connect: { item: 'GitHub', itemKind: 'toolset', instanceId: 'gh-1', reason: 'sign_in_rejected' },
  }),
];

const RUN = {
  runId: 'r-1', orgId: ORG, sessionId: 's-1', actorId: 'u-owner', harnessMajor: 2, status: 'waiting_approval',
  revision: 4, updatedAt: 900, openInteractions: [WAIT],
} as unknown as HarnessRun;

function queue(journal: JournalEvent[] = JOURNAL) {
  const timeline = () => foldTimeline(journal, 'r-1');
  return new ApprovalQueueService(
    { pendingApprovalRuns: async () => [RUN] },
    {
      authorityFor: async () => ({ approve: true, deny: true, participant: true }),
      standing: async () => ({ isApprover: false, isOrgAdmin: false }),
      administersAnyTeam: async () => false,
    } as never,
    { approvals: async () => timeline().approvals, connects: async () => timeline().connects },
  );
}

describe('WP-64: a reconnect wait in the inbox and its link', () => {
  it('WP64-IB-1: the owner’s inbox lists the wait, naming the service, why and when, as the contract says', async () => {
    const items = await queue().pending({ orgId: ORG, actorId: 'u-owner', limit: 50 });
    expect(items).to.have.length(1);
    expect(items[0]).to.deep.include({ runId: 'r-1', interactionId: WAIT, argsHidden: false, authority: { approve: true, deny: true } });
    expect(items[0]!.connect).to.deep.equal({ item: 'GitHub', itemKind: 'toolset', instanceId: 'gh-1', reason: 'sign_in_rejected', askedAt: AT });
    expect(items[0]).to.not.have.property('pattern');
    expect(pendingApprovalsSchema.safeParse({ items }).success).to.equal(true);
  });

  it('WP64-IB-2: nobody but the owner is asked to connect their account', async () => {
    expect(await queue().pending({ orgId: ORG, actorId: 'u-approver', limit: 50 })).to.deep.equal([]);
    expect(await queue().decidable({ orgId: ORG, actorId: 'u-approver' }, RUN)).to.deep.equal([]);
  });

  it('WP64-IB-3: a link to the wait shows the owner the same row; a wait already answered shows nothing', async () => {
    const one = await queue().one({ orgId: ORG, actorId: 'u-owner' }, RUN, WAIT);
    expect(one?.connect?.item).to.equal('GitHub');
    const answered = [...JOURNAL, event('interaction.resolved', { interactionId: WAIT, decision: 'denied', by: 'p', channel: 'web' }, 'node')];
    expect(await queue(answered).one({ orgId: ORG, actorId: 'u-owner' }, RUN, WAIT)).to.equal(null);
  });

  it('WP64-IB-4: the timeline and the chat’s connect part carry why it waits and when', () => {
    const [view] = foldTimeline(JOURNAL, 'r-1').connects;
    expect(view).to.deep.include({ item: 'GitHub', reason: 'sign_in_rejected', askedAt: AT, status: 'pending' });
    expect(connectPart(view!, 'r-1')['connect']).to.deep.equal({
      item: 'GitHub', itemKind: 'toolset', connectPath: '/workspace/actions', reason: 'sign_in_rejected', since: AT,
    });
    const plain = foldTimeline(
      JOURNAL.map((e) => (e.type === 'interaction.requested' ? { ...e, data: { ...e.data, connect: { item: 'Jira', itemKind: 'toolset', instanceId: 'j' } } } : e)) as JournalEvent[],
      'r-1',
    ).connects[0]!;
    expect(plain).to.not.have.property('reason');
    expect(connectPart(plain, 'r-1')['connect']).to.not.have.property('since');
  });
});
