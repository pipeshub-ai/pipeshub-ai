/**
 * QA6-F12: a sub-agent's card on its parent's stream. The card has no position
 * in the parent's journal, so a stream id says which cards the client holds:
 * ids stay unique and ordered, and a resume repeats and drops none.
 */
import { expect } from 'chai';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { FrameSink, RunStreamService, StreamHandle } from '../../../../src/modules/agent-harness/projection/stream-service';
import {
  compareStreamPositions,
  parseStreamId,
  positionalId,
  withChildState,
} from '../../../../src/modules/agent-harness/projection/stream-id';
import { parseSse, readJournal } from '../../../contracts/support/durable';
import { testCipher } from '../support/data-protection';

const ORG = 'org-1';
const PARENT = 'run-parent';
const CHILD = 'run-child';
const OTHER = 'run-other';
const SESSION = 'session-1';
const APPROVE = 'ix.approve.t1.0';
const UNKNOWN = 'ix.unknown.t1.0';
/** `child.spawned` of CHILD and OTHER in the parent's journal. */
const SPAWN = 3;
const SPAWN_OTHER = 4;

const parentEvent = (seq: number, type: string, data: Record<string, unknown>): JournalEvent => ({
  orgId: ORG,
  sessionId: SESSION,
  streamId: SESSION,
  seq,
  runId: PARENT,
  type,
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  ts: `2026-10-09T12:00:0${String(seq)}.000Z`,
  writer: 'node',
  data,
});

const spawned = (seq: number, childRunId: string, taskId: string): JournalEvent =>
  parentEvent(seq, 'child.spawned', { childRunId, invocationId: `t1.${String(seq)}`, taskId, grantDigest: `sha256:${'a'.repeat(64)}` });

const ended = (seq: number, childRunId: string): JournalEvent =>
  parentEvent(seq, 'child.completed', {
    childRunId,
    outcome: 'failed',
    errorType: 'deadline_exceeded',
    resultRef: { ref: 'r', hash: `sha256:${'b'.repeat(64)}`, size: 1 },
    labels: { integrity: 'trusted' },
  });

const golden = readJournal('agent_quick_tool_call');
const childBase = (childRunId: string): JournalEvent[] =>
  golden.events.filter((e) => e.seq <= 13).map((e) => ({ ...e, orgId: ORG, sessionId: SESSION, streamId: childRunId, runId: childRunId }));
const childEvent = (
  seq: number,
  type: string,
  writer: 'worker' | 'node',
  data: Record<string, unknown>,
  childRunId = CHILD,
): JournalEvent => ({ ...(childBase(childRunId)[12] as JournalEvent), seq, type, writer, data });

const asked = (seq: number, interactionId = APPROVE, childRunId = CHILD): JournalEvent =>
  childEvent(seq, 'interaction.requested', 'worker', { interactionId, kind: 'approval' }, childRunId);
const decided = (seq: number, decision: string, interactionId = APPROVE): JournalEvent =>
  childEvent(seq, 'interaction.resolved', 'node', { interactionId, decision, by: 'p', channel: 'web' });

const settle = async (): Promise<void> => {
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

interface Sent {
  id: string;
  name: string | undefined;
  value: Record<string, unknown>;
}

class Sink implements FrameSink {
  body = '';
  handle: StreamHandle | undefined;
  /** The client goes away; what it has stays readable. */
  drop(): this {
    this.handle?.close();
    return this;
  }
  write(chunk: string): boolean {
    this.body += chunk;
    return true;
  }
  end(): void {}
  onDrain(): void {}
  get pending(): number {
    return 0;
  }
  sent(): Sent[] {
    return parseSse(this.body).map(({ id, frame }) => ({
      id: id as string,
      name: (frame as AguiFrame)['name'] as string | undefined,
      value: ((frame as AguiFrame)['value'] ?? {}) as Record<string, unknown>,
    }));
  }
  named(name: string): Sent[] {
    return this.sent().filter((frame) => frame.name === name);
  }
  cards(): unknown[] {
    return this.named('approval_requested').map((frame) => frame.value['interactionId']);
  }
  held(): unknown {
    return parseStreamId(this.lastId)?.held;
  }
  get lastId(): string {
    return this.sent().at(-1)?.id as string;
  }
}

class World {
  readonly events: JournalEvent[] = [
    parentEvent(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
    parentEvent(2, 'turn.started', { turn: 1 }),
    spawned(SPAWN, CHILD, 'research'),
    ...childBase(CHILD),
  ];
  readonly tail = new InMemoryJournalTail();
  readonly service = new RunStreamService({
    journal: {
      readRun: (_org: string, runId: string, after: number, limit: number) =>
        Promise.resolve(this.events.filter((e) => e.runId === runId && e.seq > after).slice(0, limit)),
    },
    journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
    tail: this.tail,
    conversations: { finishedConversation: () => Promise.resolve({ _id: SESSION, messages: [] }) },
    liveBus: null,
  });
  readonly run = {
    runId: PARENT,
    orgId: ORG,
    sessionId: SESSION,
    actorId: 'user-1',
    input: { query: 'q' },
    createdAt: 1,
    updatedAt: 2,
  } as unknown as HarnessRun;

  async open(lastEventId?: string, audience: Audience = 'owner'): Promise<Sink> {
    const sink = new Sink();
    sink.handle = this.service.open({ run: this.run, audience, ...(lastEventId !== undefined ? { lastEventId } : {}) }, sink);
    await settle();
    return sink;
  }

  async commit(...events: JournalEvent[]): Promise<void> {
    for (const event of events) {
      this.events.push(event);
      this.tail.changed(ORG, event.runId as string);
    }
    await settle();
  }

  /** While nobody is subscribed. */
  meanwhile(...events: JournalEvent[]): void {
    this.events.push(...events);
  }
}

const HELD = `^${String(SPAWN)}.14`;

describe('stream ids: the sub-agent cards a client holds (QA6-F12)', () => {
  it('F12-2: an id names the held cards and a state frame\'s place, and parses back to the same position', () => {
    const position = 'j:22.1/3.1.4~2.1';
    const id = withChildState(position, [{ spawnSeq: 9, requestSeq: 2 }, { spawnSeq: 3, requestSeq: 14 }], 2);
    expect(id).to.equal('j:22.1/3.1.4~2.1^3.14^9.2+2');
    expect(positionalId(id)).to.equal(position);
    expect(parseStreamId(id)).to.deep.equal({
      ...parseStreamId(position),
      held: [{ spawnSeq: 3, requestSeq: 14 }, { spawnSeq: 9, requestSeq: 2 }],
      stated: 2,
    });
    expect(parseStreamId(position)).to.include({ stated: 0 }).and.to.deep.include({ held: [] });
  });

  it('F12-2: naming the same cards again, in any order, is the same id', () => {
    const cards = [{ spawnSeq: 3, requestSeq: 14 }, { spawnSeq: 3, requestSeq: 9 }];
    expect(withChildState('d:5.1.2.1/3.1', cards)).to.equal('d:5.1.2.1/3.1^3.9^3.14');
    expect(withChildState(withChildState('d:5.1.2.1/3.1', cards, 4), [...cards].reverse(), 0)).to.equal('d:5.1.2.1/3.1^3.9^3.14');
    expect(withChildState('j:1.1/0.0.0', [cards[0]!, cards[0]!])).to.equal('j:1.1/0.0.0^3.14');
  });

  it('F12-2: the end of a lost message keeps its position under the cards', () => {
    const cursor = parseStreamId('a:d:5.1.2.1/3.1^3.14+1');
    expect(cursor).to.deep.include({ abandoned: true, held: [{ spawnSeq: 3, requestSeq: 14 }], stated: 1 });
    expect(cursor?.position).to.deep.equal({ seq: 5, k: 1, n: 2, m: 1 });
  });

  for (const bad of ['j:1.1/0.0.0^3', 'j:1.1/0.0.0^3.x', 'j:1.1/0.0.0+', 'j:1.1/0.0.0+1^3.14', '^3.14', `j:1.1/0.0.0^3.${'9'.repeat(30)}`]) {
    it(`F12-8: ${bad.slice(0, 24)} is no id this stream wrote`, () => {
      expect(parseStreamId(bad)).to.equal(null);
    });
  }
});

describe('agent-harness stream: a sub-agent\'s card across reconnects (QA6-F12)', () => {
  it('F12-1: ids on one connection are unique and ordered; a card is placed after its positional frame', async () => {
    const world = new World();
    const sink = await world.open();
    const before = sink.lastId;
    await world.commit(asked(14));
    await world.commit(spawned(SPAWN_OTHER, OTHER, 'writer'));
    await world.commit(decided(15, 'approved'));
    await world.commit(parentEvent(5, 'run.cancelled', { reason: 'user' }));
    const ids = sink.sent().map((frame) => frame.id);
    expect(new Set(ids).size, ids.join(' ')).to.equal(ids.length);
    // CTRESULTS: the child's steps are said before its card, after the same positional frame.
    const card = sink.named('approval_requested')[0]?.id as string;
    expect(card.startsWith(`${positionalId(before)}${HELD}+`), card).to.equal(true);
    const closed = sink.named('approval_resolved')[0]?.id as string;
    expect(parseStreamId(closed), 'nothing is held once it is decided').to.deep.include({ held: [] });
    expect(parseStreamId(closed)?.stated).to.be.greaterThan(0);
    const order = ids.map((id) => {
      const cursor = parseStreamId(id);
      expect(cursor, id).to.not.equal(null);
      return cursor!;
    });
    for (let i = 1; i < order.length; i += 1) {
      const moved = compareStreamPositions(order[i]!.position, order[i - 1]!.position);
      expect(moved > 0 || (moved === 0 && order[i]!.stated > order[i - 1]!.stated), `${ids[i - 1]} then ${ids[i]}`).to.equal(true);
    }
    expect(positionalId(sink.lastId), 'a positional frame follows the cards').to.not.equal(positionalId(closed));
    expect(sink.lastId).to.equal(positionalId(sink.lastId));
  });

  it('F12-2: every positional frame sent while the card is open names it', async () => {
    const world = new World();
    world.meanwhile(asked(14));
    const sink = await world.open();
    const card = sink.sent().findIndex((frame) => frame.name === 'approval_requested');
    await world.commit(parentEvent(5, 'run.cancelled', { reason: 'user' }));
    const after = sink.sent().slice(card);
    expect(after.length).to.be.greaterThan(1);
    for (const frame of after) expect(parseStreamId(frame.id)?.held, frame.id).to.deep.equal([{ spawnSeq: SPAWN, requestSeq: 14 }]);
    expect(parseStreamId(sink.lastId)?.stated, 'the last one is positional').to.equal(0);
  });

  it('F12-3: a resume from the card\'s id while it is open does not announce it again', async () => {
    const world = new World();
    const first = await world.open();
    await world.commit(asked(14));
    const again = await world.open(first.drop().lastId);
    expect(again.cards()).to.deep.equal([]);
    expect(again.named('approval_resolved')).to.deep.equal([]);
    await world.commit(decided(15, 'approved'));
    expect(again.named('approval_resolved').map((frame) => frame.value)).to.deep.equal([{ interactionId: APPROVE, decision: 'approved', childRunId: CHILD }]);
  });

  it('F12-3: the ids after a resume go on from the one resumed from', async () => {
    const world = new World();
    const first = await world.open();
    await world.commit(asked(14));
    const again = await world.open(first.drop().lastId);
    await world.commit(decided(15, 'approved'));
    const seen = new Set(first.sent().map((frame) => frame.id));
    for (const frame of again.sent()) expect(seen.has(frame.id), frame.id).to.equal(false);
    // A resumed stream says the child's steps again (the chat drops those it has), then the decision.
    const resolved = again.named('approval_resolved')[0]?.id as string;
    expect(positionalId(resolved)).to.equal(positionalId(first.lastId));
    expect(parseStreamId(resolved)?.stated).to.be.greaterThan(1);
  });

  it('F12-4: decided while the client was away: its decision is sent once', async () => {
    const world = new World();
    const first = await world.open();
    await world.commit(asked(14));
    first.drop();
    world.meanwhile(decided(15, 'denied'));
    const again = await world.open(first.lastId);
    expect(again.cards()).to.deep.equal([]);
    expect(again.named('approval_resolved').map((frame) => frame.value)).to.deep.equal([{ interactionId: APPROVE, decision: 'denied', childRunId: CHILD }]);
    expect(again.held()).to.deep.equal([]);
  });

  it('F12-5: the child ended while the client was away: the held card closes as expired', async () => {
    const world = new World();
    const first = await world.open();
    await world.commit(asked(14));
    first.drop();
    world.meanwhile(ended(5, CHILD));
    const again = await world.open(first.lastId);
    expect(again.named('approval_resolved').map((frame) => frame.value)).to.deep.equal([{ interactionId: APPROVE, decision: 'expired', childRunId: CHILD }]);
    expect(again.held()).to.deep.equal([]);
  });

  it('F12-5: decided, then the child ended: the decision, not expired', async () => {
    const world = new World();
    const first = await world.open();
    await world.commit(asked(14));
    first.drop();
    world.meanwhile(decided(15, 'approved'), ended(5, CHILD));
    const again = await world.open(first.lastId);
    expect(again.named('approval_resolved').map((frame) => frame.value)).to.deep.equal([{ interactionId: APPROVE, decision: 'approved', childRunId: CHILD }]);
  });

  it('F12-6: a resume from an id that holds nothing is told every open card, once', async () => {
    const world = new World();
    const first = await world.open();
    await world.commit(asked(14));
    const card = first.drop().named('approval_requested')[0]!;
    for (const lost of [positionalId(card.id), undefined]) {
      const again = await world.open(lost);
      expect(again.drop().cards(), String(lost)).to.deep.equal([APPROVE]);
    }
  });

  it('F12-7: a card asked while the client was away is announced once; the one it holds is not', async () => {
    const world = new World();
    world.meanwhile(spawned(SPAWN_OTHER, OTHER, 'writer'), ...childBase(OTHER));
    const first = await world.open();
    await world.commit(asked(14));
    first.drop();
    world.meanwhile(asked(15, UNKNOWN), asked(14, APPROVE, OTHER));
    const again = await world.open(first.lastId);
    expect(again.named('approval_requested').map((frame) => [frame.value['childRunId'], frame.value['interactionId']])).to.deep.equal([[OTHER, APPROVE]]);
    expect(again.named('unknown_effect').map((frame) => [frame.value['childRunId'], frame.value['interactionId']])).to.deep.equal([[CHILD, UNKNOWN]]);
    expect(again.held()).to.deep.equal([
      { spawnSeq: SPAWN, requestSeq: 14 },
      { spawnSeq: SPAWN, requestSeq: 15 },
      { spawnSeq: SPAWN_OTHER, requestSeq: 14 },
    ]);
  });

  it('F12-8: a held entry that names no card or no child says nothing and is dropped', async () => {
    const world = new World();
    const first = await world.open();
    const forged = `${first.lastId}^${String(SPAWN)}.7^${String(SPAWN)}.999^77.1`;
    const again = await world.open(forged);
    expect(again.cards()).to.deep.equal([]);
    expect(again.named('approval_resolved')).to.deep.equal([]);
    await world.commit(asked(14));
    expect(again.cards()).to.deep.equal([APPROVE]);
    expect(again.held()).to.deep.equal([{ spawnSeq: SPAWN, requestSeq: 14 }]);
  });

  it('F12-8: a viewer who is not the owner gets plain ids, whatever cursor it sends', async () => {
    const world = new World();
    world.meanwhile(asked(14));
    const owner = await world.open();
    const shared = await world.open(owner.lastId, 'shared');
    await world.commit(parentEvent(5, 'run.cancelled', { reason: 'user' }));
    expect(shared.sent().length).to.be.greaterThan(0);
    for (const frame of shared.sent()) expect(frame.id).to.equal(positionalId(frame.id));
  });
});
