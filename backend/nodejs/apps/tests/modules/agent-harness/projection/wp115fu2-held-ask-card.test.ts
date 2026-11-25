/**
 * WP115-FU-2 (ADR-GRANT-01 D7): an ask a conversation grant the run already holds covers is answered by Node at
 * once (`answerHeld`), so the live chat never draws its card as an open ask: the card goes out with its answer,
 * already decided, as a reload shows it. An ask the held grant does not cover, or one Node did not answer in time,
 * is drawn as before.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame } from '../../../../src/modules/agent-harness/projection/frames';
import {
  HELD_ASK_GRACE_MS,
  RunProjector,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { ChildInteractions } from '../../../../src/modules/agent-harness/projection/child-interactions';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const asked = base.events.filter((e) => e.seq <= 12);
const last = asked[asked.length - 1] as JournalEvent;
const IX = 'ix.approve.t1.0';
const KEY = `sha256:${'2'.repeat(64)}`;
const TOOL = 'slack@default/send_message';
const NOW = Date.parse('2026-11-09T12:00:00.000Z');
const at = (ms: number): string => new Date(NOW + ms).toISOString();

const event = (
  seq: number,
  type: string,
  writer: 'worker' | 'node',
  data: Record<string, unknown>,
  ts = at(0),
  runId = base.runId,
): JournalEvent => ({ ...last, seq, type, writer, critical: type !== 'interaction.explained', data, ts, runId });

const copy = (sensitivity: string, seq = 13): JournalEvent =>
  event(seq, 'interaction.granted', 'node', {
    interactionId: 'ix.approve.t0.0',
    scope: 'conversation',
    toolId: TOOL,
    key: KEY,
    sensitivity,
    by: 'p_owner',
    fromRunId: 'run-sibling',
  });
const ask = (sensitivity: string, ts = at(0)): JournalEvent[] => [
  event(
    14,
    'interaction.explained',
    'worker',
    {
      interactionId: IX,
      rule: 'flow_external_write',
      toolId: TOOL,
      sensitivity,
      destination: { label: '#sales', key: KEY },
      conversationGrant: true,
    },
    ts,
  ),
  event(15, 'interaction.requested', 'worker', { interactionId: IX, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }, ts),
];
const answer: JournalEvent[] = [
  event(16, 'interaction.resolved', 'node', { interactionId: IX, decision: 'approved', by: 'p_owner', channel: 'system' }, at(40)),
  event(17, 'interaction.granted', 'node', { interactionId: IX, scope: 'conversation', toolId: TOOL, key: KEY, sensitivity: 'private', by: 'p_owner' }, at(40)),
];

const projector = (now = NOW): RunProjector => {
  const p = new RunProjector({ ...projectorContext(base, 'owner'), now: () => now });
  p.begin();
  return p;
};
const frames = (p: RunProjector, events: JournalEvent[]): AguiFrame[] => p.project(events).map((f) => f.frame);
const names = (list: AguiFrame[]): string[] =>
  list.filter((f) => f.type === 'CUSTOM').map((f) => String(f['name']));
const cards = (list: AguiFrame[]): AguiFrame[] => list.filter((f) => f['name'] === 'approval_requested');
const waitingCounts = (list: AguiFrame[]): number[] =>
  list.filter((f) => f['name'] === 'interaction_waiting').map((f) => (f['value'] as { count: number }).count);

describe('agent-harness projection: an ask a held grant answers draws no open card (WP115-FU-2)', () => {
  it('HA-1: the covered ask raises no card and no waiting count while Node answers it', () => {
    const p = projector();
    const raised = frames(p, [...asked, copy('private'), ...ask('private')]);
    expect(cards(raised)).to.deep.equal([]);
    expect(waitingCounts(raised).filter((n) => n > 0)).to.deep.equal([]);
    expect(p.heldUntil()).to.equal(NOW + HELD_ASK_GRACE_MS);
  });

  it('HA-2: the card goes out with its answer, already decided, as a reload shows it', () => {
    const p = projector();
    frames(p, [...asked, copy('private'), ...ask('private')]);
    const answered = frames(p, answer);
    const shown = names(answered);
    const card = shown.indexOf('approval_requested');
    expect(card).to.be.greaterThan(-1);
    expect(shown[card + 1]).to.equal('approval_resolved');
    expect(cards(answered)[0]?.['value']).to.include({ interactionId: IX });
    expect(waitingCounts(answered).every((n) => n === 0)).to.equal(true);
    expect(p.heldUntil()).to.equal(null);
    // The card's value is the one an unheld ask would have carried.
    const plain = projector();
    const unheld = cards(frames(plain, [...asked, ...ask('private')]))[0]?.['value'];
    expect(cards(answered)[0]?.['value']).to.deep.equal(unheld);
  });

  it('HA-3: an ask no held grant covers is drawn at once, as before', () => {
    for (const events of [
      [...asked, ...ask('private')],
      // A grant given over public data never answers an ask over private data.
      [...asked, copy('public'), ...ask('private')],
    ]) {
      const p = projector();
      const raised = frames(p, events);
      expect(cards(raised)).to.have.length(1);
      expect(waitingCounts(raised)).to.include(1);
      expect(p.heldUntil()).to.equal(null);
    }
  });

  it('HA-4: an ask Node did not answer within the grace is drawn then, with its waiting count', () => {
    const p = projector();
    frames(p, [...asked, copy('private'), ...ask('private')]);
    expect(p.releaseHeld(NOW + HELD_ASK_GRACE_MS - 1).map((f) => f.frame)).to.deep.equal([]);
    const released = p.releaseHeld(NOW + HELD_ASK_GRACE_MS).map((f) => f.frame);
    expect(cards(released)).to.have.length(1);
    expect(waitingCounts(released)).to.deep.equal([1]);
    expect(p.heldUntil()).to.equal(null);
    // Its later answer resolves the card the person now holds, once.
    const answered = frames(p, answer);
    expect(cards(answered)).to.deep.equal([]);
    expect(names(answered)).to.include('approval_resolved');
  });

  it('HA-5: a replay of a covered ask older than the grace draws it at once (nothing will answer it now)', () => {
    const p = projector(NOW + 60_000);
    const replayed = frames(p, [...asked, copy('private'), ...ask('private', at(0))]);
    expect(cards(replayed)).to.have.length(1);
    expect(p.heldUntil()).to.equal(null);
  });

  it('HA-6: a held ask revoked meanwhile is drawn at once', () => {
    const p = projector();
    const revoked = event(13, 'interaction.grant_revoked', 'node', { toolId: TOOL, key: KEY });
    const raised = frames(p, [...asked, copy('private', 13), { ...revoked, seq: 13.5 }, ...ask('private')]);
    expect(cards(raised)).to.have.length(1);
  });

  it('HA-7: a child ask a held grant covers reaches the parent with its answer, not before', async () => {
    const CHILD = 'run-child';
    const journal: JournalEvent[] = [
      ...asked.map((e) => ({ ...e, runId: CHILD })),
      { ...copy('private'), runId: CHILD },
      ...ask('private').map((e) => ({ ...e, runId: CHILD })),
    ];
    const child = new ChildInteractions(async (_id, after) => journal.filter((e) => e.seq > after), base.runId, [], () => NOW);
    child.track([{ childRunId: CHILD, role: 'researcher', spawnSeq: 3, live: true }]);
    const raised = (await child.pull(CHILD)).map((f) => f.frame);
    expect(cards(raised)).to.deep.equal([]);
    journal.push(...answer.map((e) => ({ ...e, runId: CHILD })));
    const answered = names((await child.pull(CHILD)).map((f) => f.frame));
    const card = answered.indexOf('approval_requested');
    expect(card).to.be.greaterThan(-1);
    expect(answered.slice(card + 1)).to.include('approval_resolved');
  });
});
