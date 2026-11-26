/**
 * UX3 CA-1..CA-4: a sub-agent's approval (or unknown effect) reaches its
 * parent's owner stream, attributed to the child, and only the owner's.
 */
import { expect } from 'chai';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { FrameSink, RunStreamService } from '../../../../src/modules/agent-harness/projection/stream-service';
import { parseSse, readJournal } from '../../../contracts/support/durable';
import { testCipher } from '../support/data-protection';

const ORG = 'org-1';
const PARENT = 'run-parent';
const CHILD = 'run-child';
const SESSION = 'session-1';
const APPROVE = 'ix.approve.t1.0';
const UNKNOWN = 'ix.unknown.t1.0';

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

const golden = readJournal('agent_quick_tool_call');
const childBase: JournalEvent[] = golden.events
  .filter((e) => e.seq <= 13)
  .map((e) => ({ ...e, orgId: ORG, sessionId: SESSION, streamId: CHILD, runId: CHILD }));
const childEvent = (seq: number, type: string, writer: 'worker' | 'node', data: Record<string, unknown>): JournalEvent => ({
  ...(childBase[childBase.length - 1] as JournalEvent),
  seq,
  type,
  writer,
  data,
});

const settle = async (): Promise<void> => {
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

class Sink implements FrameSink {
  body = '';
  write(chunk: string): boolean {
    this.body += chunk;
    return true;
  }
  end(): void {}
  onDrain(): void {}
  get pending(): number {
    return 0;
  }
  custom(name: string): Array<Record<string, unknown>> {
    return parseSse(this.body)
      .map((f) => f.frame as AguiFrame)
      .filter((f) => f.type === 'CUSTOM' && f['name'] === name)
      .map((f) => f['value'] as Record<string, unknown>);
  }
}

class World {
  readonly events: JournalEvent[] = [
    parentEvent(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
    parentEvent(2, 'turn.started', { turn: 1 }),
    parentEvent(3, 'child.spawned', {
      childRunId: CHILD,
      invocationId: 't1.0',
      taskId: 'research',
      grantDigest: `sha256:${'a'.repeat(64)}`,
    }),
    ...childBase,
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

  open(audience: Audience = 'owner'): Sink {
    const sink = new Sink();
    this.service.open({ run: this.run, audience }, sink);
    return sink;
  }

  commit(event: JournalEvent): void {
    this.events.push(event);
    this.tail.changed(ORG, event.runId as string);
  }
}

const asked = (seq: number) => childEvent(seq, 'interaction.requested', 'worker', { interactionId: APPROVE, kind: 'approval' });

describe('agent-harness stream: a sub-agent’s approvals in the main thread (UX3)', () => {
  it('CA-1: a child’s approval reaches the owner attributed to the child; its decision follows', async () => {
    const world = new World();
    const sink = world.open();
    await settle();
    expect(sink.custom('approval_requested')).to.deep.equal([]);
    world.commit(asked(14));
    await settle();
    const [card] = sink.custom('approval_requested');
    expect(card).to.include({ interactionId: APPROVE, childRunId: CHILD, childRole: 'research' });
    expect(card?.['toolName']).to.be.a('string');
    world.commit(childEvent(15, 'interaction.resolved', 'node', { interactionId: APPROVE, decision: 'approved', by: 'p', channel: 'web' }));
    await settle();
    expect(sink.custom('approval_resolved')).to.deep.equal([{ interactionId: APPROVE, decision: 'approved', childRunId: CHILD }]);
  });

  it('CA-1 (UXINT-3): an engine child’s card names its engine, from the child’s pinned spec', async () => {
    const world = new World();
    for (const [i, e] of world.events.entries()) {
      if (e.runId !== CHILD || e.type !== 'run.spec_pinned') continue;
      const spec = { ...(e.data?.['spec'] as Record<string, unknown>), executor: { kind: 'engine', engine: { kind: 'codex' } } };
      world.events[i] = { ...e, data: { ...e.data, spec } };
    }
    world.events.push(asked(14));
    const sink = world.open();
    await settle();
    expect(sink.custom('approval_requested')[0]).to.include({ childRunId: CHILD, childEngine: 'codex' });
  });

  it('CA-2: a subscription opened while it is pending announces it once', async () => {
    const world = new World();
    world.events.push(asked(14));
    const sink = world.open();
    await settle();
    expect(sink.custom('approval_requested').map((v) => v['interactionId'])).to.deep.equal([APPROVE]);
  });

  it('CA-2: one already decided is not announced', async () => {
    const world = new World();
    world.events.push(asked(14), childEvent(15, 'interaction.resolved', 'node', { interactionId: APPROVE, decision: 'denied', by: 'p', channel: 'web' }));
    const sink = world.open();
    await settle();
    expect(sink.custom('approval_requested')).to.deep.equal([]);
  });

  it('CA-3: a child’s unknown effect is announced as unknown_effect', async () => {
    const world = new World();
    world.events.push(childEvent(14, 'interaction.requested', 'worker', { interactionId: UNKNOWN, kind: 'approval' }));
    const sink = world.open();
    await settle();
    expect(sink.custom('unknown_effect')[0]).to.include({ interactionId: UNKNOWN, childRunId: CHILD, childRole: 'research' });
  });

  it('CA-4: a shared viewer gets nothing of it', async () => {
    const world = new World();
    world.events.push(asked(14));
    const sink = world.open('shared');
    await settle();
    expect(sink.custom('approval_requested')).to.deep.equal([]);
  });

  it('CA-4: a child that ends closes its open card as expired and is no longer followed', async () => {
    const world = new World();
    world.events.push(asked(14));
    const sink = world.open();
    await settle();
    world.commit(parentEvent(4, 'child.completed', { childRunId: CHILD, outcome: 'failed', errorType: 'deadline_exceeded', resultRef: { ref: 'r', hash: `sha256:${'b'.repeat(64)}`, size: 1 }, labels: { integrity: 'trusted' } }));
    await settle();
    expect(sink.custom('approval_resolved')).to.deep.equal([{ interactionId: APPROVE, decision: 'expired', childRunId: CHILD }]);
    world.commit(childEvent(16, 'interaction.requested', 'worker', { interactionId: 'ix.approve.t1.1', kind: 'approval' }));
    await settle();
    expect(sink.custom('approval_requested')).to.have.length(1);
  });
});
