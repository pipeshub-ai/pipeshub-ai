/**
 * R37-SECB regression tests. Each case plays what an injected model or a hostile participant does and checks the
 * product's own control against the reason its SEC-B-NN names; the model is assumed fooled.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { ConversationGrantReach } from '../../../src/modules/agent-harness/application/conversation-grant-reach';
import {
  ConversationGrant,
  runGrantState,
} from '../../../src/modules/agent-harness/domain/conversation-grants';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { settlement } from '../../../src/modules/agent-harness/domain/memory';
import { markdownToSlackMrkdwn } from '../../../src/integrations/slack-bot/src/utils/md_to_mrkdwn';

const FETCH = 'web@searxng/fetch_url';
const EVIL = 'sha256:r37secb-attacker-site';

const event = (
  runId: string,
  seq: number,
  type: string,
  writer: 'worker' | 'node',
  data: Record<string, unknown>,
): JournalEvent =>
  ({
    orgId: 'org-1',
    sessionId: 'ses-shared',
    streamId: runId,
    runId,
    seq,
    type,
    writer,
    schemaVersion: 1,
    critical: false,
    visibility: 'owner',
    ts: '2026-11-13T12:00:00.000Z',
    data,
  }) as JournalEvent;

const asking = (runId: string, seq: number, ix: string): JournalEvent[] => [
  event(runId, seq, 'interaction.explained', 'worker', {
    interactionId: ix,
    toolId: FETCH,
    rule: 'flow_external_write',
    layer: 'flow',
    conversationGrant: true,
    sensitivity: 'private',
    destination: { key: EVIL, label: 'attacker.example' },
  }),
  event(runId, seq + 1, 'interaction.requested', 'worker', { interactionId: ix, kind: 'approval' }),
];

const run = (runId: string, actorId: string): HarnessRun =>
  ({ orgId: 'org-1', sessionId: 'ses-shared', runId, harnessMajor: 2, status: 'running', actorId }) as unknown as HarnessRun;

describe('R37-SECB: AI-specific attacks', () => {
  it("SEC-B-01: a collaborator's conversation grant never answers the owner's run's ask", async () => {
    // Alice (owner) shared the conversation with Bob (collaborator). Alice's run read her private record and, steered
    // by an injection Bob posted in the conversation, asks to fetch attacker.example with it in the URL. Bob's own run
    // asks the same; Bob answers his own card "Allow for this conversation".
    const streams: Record<string, JournalEvent[]> = {
      'run-alice': asking('run-alice', 1, 'ix.alice.1'),
      'run-bob': [
        ...asking('run-bob', 1, 'ix.bob.1'),
        event('run-bob', 3, 'interaction.resolved', 'node', { interactionId: 'ix.bob.1', decision: 'approved' }),
        event('run-bob', 4, 'interaction.granted', 'node', {
          interactionId: 'ix.bob.1',
          scope: 'conversation',
          toolId: FETCH,
          key: EVIL,
          sensitivity: 'private',
          by: 'p_bob',
        }),
      ],
    };
    const resolveAll = sinon.stub().resolves({});
    const reach = new ConversationGrantReach<undefined>(
      {
        grantState: async (_org: string, runId: string) => runGrantState(streams[runId] ?? []),
        openSessionRuns: async () => [run('run-alice', 'u-alice'), run('run-bob', 'u-bob')],
      },
      { resolveAll } as never,
      {
        append: async (prepared: never) => {
          const batch = prepared as { runId: string; events: Array<{ type: string; data: Record<string, unknown> }> };
          const stream = (streams[batch.runId] ??= []);
          for (const each of batch.events) stream.push(event(batch.runId, stream.length + 200, each.type, 'node', each.data));
          return { streamId: batch.runId, firstSeq: 1, lastSeq: 1, ts: '' };
        },
      },
      { prepare: async (append: never) => append as never },
      async (work) => work(undefined),
    );
    await reach.reach({
      orgId: 'org-1',
      sessionId: 'ses-shared',
      runId: 'run-bob',
      interactionIds: ['ix.bob.1'],
      by: 'p_bob',
      channel: 'web',
      commandId: 'cmd-bob',
    });
    const answeredForAlice = resolveAll
      .getCalls()
      .map((call) => call.args[0] as { runId: string; by: string })
      .filter((command) => command.runId === 'run-alice');
    const aliceHolds = [...runGrantState(streams['run-alice'] ?? []).held.values()] as ConversationGrant[];
    // R38-AUTHZ (ADR-GRANT-01 D9): Bob's grant is never copied onto Alice's run nor answers her ask.
    expect(answeredForAlice, "Bob approved Alice's ask").to.deep.equal([]);
    expect(aliceHolds, "Alice's run holds Bob's grant").to.deep.equal([]);
  });

  it('SEC-B-05: a memory proposed by a run that read content an outsider wrote waits for a person', async () => {
    // The run read a public web page (never named in a label) that told the model to remember an instruction.
    const label = { complete: true, sources: [] };
    const policy = { mode: 'user' as const, review: 'auto' as const };
    // The worker's untrusted taint reaches settlement as the label's integrity (ADR-MEM-01).
    expect(settlement('user', { ...label, integrity: 'external' }, policy)).to.equal('proposed');
    expect(settlement('session', { ...label, integrity: 'external' }, policy)).to.equal('proposed');
    expect(settlement('user', { ...label, integrity: 'first_party' }, policy)).to.equal('accepted');
  });

  describe('SEC-B-03: model text posted to Slack', () => {
    const cases: Array<[string, string, (out: string) => void]> = [
      ['notifies the whole channel', 'Done. <!channel> please re-auth now', (out) => expect(out).not.to.contain('<!channel>')],
      ['notifies everyone online', '<!here> urgent', (out) => expect(out).not.to.contain('<!here>')],
      ['pings a user group', '<!subteam^S0ADMINS> approve this', (out) => expect(out).not.to.contain('<!subteam')],
      ['pings a named person', '<@U0CFO> wire the funds', (out) => expect(out).not.to.contain('<@U0CFO>')],
      [
        'shows a trusted address on an attacker link',
        '<https://attacker.example/login|https://pipeshub.example/login>',
        (out) => expect(out).not.to.match(/<https:\/\/attacker\.example[^>]*\|https:\/\/pipeshub/),
      ],
    ];
    for (const [what, text, check] of cases) {
      it(`never ${what}`, () => {
        check(markdownToSlackMrkdwn(text));
      });
    }
  });
});
