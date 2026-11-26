/**
 * CTRESULTS: the parent's stream said a code task's child started and ended, nothing between, so the chat
 * showed none of its work while it ran. The owner's stream now says the child's steps with their results, under
 * the child's run id: an engine child's progress lines, a loop child's calls; a shared viewer gets none of it.
 */
import { expect } from 'chai';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { RunStreamService, FrameSink } from '../../../../src/modules/agent-harness/projection/stream-service';
import { parseSse, readJournal } from '../../../contracts/support/durable';
import { testCipher } from '../support/data-protection';

const ORG = 'org-1';
const PARENT = 'run-parent';
const CHILD = 'run-child';
const SESSION = 'session-1';

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
  ts: `2026-10-24T02:00:0${String(seq)}.000Z`,
  writer: 'node',
  data,
});

const golden = readJournal('agent_quick_tool_call');
/** A loop child: its journal up to its call's arguments (13), then the call's outcome (14..16). */
const loopChild = (until: number): JournalEvent[] =>
  golden.events
    .filter((e) => e.seq <= until)
    .map((e) => ({ ...e, orgId: ORG, sessionId: SESSION, streamId: CHILD, runId: CHILD }));

const childBase = loopChild(8);
const progress = (seq: number, kind: string, text: string, data: Record<string, unknown>): JournalEvent => ({
  ...(childBase[childBase.length - 1] as JournalEvent),
  seq,
  type: 'x.pipeshub.engine.progress',
  writer: 'worker',
  critical: false,
  data: { tool: 'code_task', taskId: CHILD, kind, text, data, turnId: 'trn_1', seq: seq * 10 },
});

/** An opencode child: a file written, a command with its output and exit, a gateway call with its result. */
const ENGINE_STEPS = [
  progress(9, 'file', 'hello.py', { change: 'modified' }),
  progress(10, 'command', 'python3 hello.py', { callId: 'c1' }),
  progress(11, 'output', '4\n', { callId: 'c1', stream: 'stdout', truncated: false }),
  progress(12, 'command_done', '', { callId: 'c1', exitCode: 0, status: 'completed' }),
  progress(13, 'tool', 'lookup_note', { callId: 't1' }),
  progress(14, 'tool_result', 'lookup_note', { callId: 't1', status: 'completed', result: 'due Friday', truncated: false }),
];

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
  frames(): AguiFrame[] {
    return parseSse(this.body).map((f) => f.frame as AguiFrame);
  }
  ofChild(): AguiFrame[] {
    return this.frames().filter((f) => f['runId'] === CHILD && f.type !== 'STATE_SNAPSHOT' && f.type !== 'RUN_STARTED' && f.type !== 'RUN_FINISHED');
  }
}

class World {
  readonly events: JournalEvent[];
  readonly tail = new InMemoryJournalTail();
  readonly service: RunStreamService;
  readonly run = { runId: PARENT, orgId: ORG, sessionId: SESSION, actorId: 'user-1', input: { query: 'q' }, createdAt: 1, updatedAt: 2 } as unknown as HarnessRun;

  constructor(child: JournalEvent[]) {
    this.events = [
      parentEvent(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
      parentEvent(2, 'turn.started', { turn: 1 }),
      parentEvent(3, 'child.spawned', { childRunId: CHILD, invocationId: 't1.0', taskId: 'code', grantDigest: `sha256:${'a'.repeat(64)}` }),
      ...child,
    ];
    this.service = new RunStreamService({
      journal: {
        readRun: (_org: string, runId: string, after: number, limit: number) =>
          Promise.resolve(this.events.filter((e) => e.runId === runId && e.seq > after).slice(0, limit)),
      },
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      tail: this.tail,
      conversations: { finishedConversation: () => Promise.resolve({ _id: SESSION, messages: [] }) },
      liveBus: null,
    });
  }

  open(audience: Audience = 'owner'): Sink {
    const sink = new Sink();
    this.service.open({ run: this.run, audience }, sink);
    return sink;
  }

  commit(...events: JournalEvent[]): void {
    this.events.push(...events);
    for (const event of events) this.tail.changed(ORG, event.runId as string);
  }
}

const value = (frame: AguiFrame | undefined): Record<string, unknown> => (frame?.['value'] ?? {}) as Record<string, unknown>;

describe('agent-harness stream: a code task child\'s steps on its parent\'s stream (CTRESULTS)', () => {
  it('an engine child\'s command, its output and exit, its file and its tool\'s result reach the owner as it journals them', async () => {
    const world = new World(childBase);
    const sink = world.open();
    await settle();
    expect(sink.ofChild()).to.deep.equal([]);
    world.commit(...ENGINE_STEPS.slice(0, 2));
    await settle();
    expect(sink.ofChild().map((f) => value(f)['kind'])).to.deep.equal(['file', 'command']);
    world.commit(...ENGINE_STEPS.slice(2));
    await settle();
    const lines = sink.ofChild();
    expect(lines.every((f) => f.type === 'CUSTOM' && f['name'] === 'engine_progress')).to.equal(true);
    expect(lines.map((f) => value(f)['kind'])).to.deep.equal(['file', 'command', 'output', 'command_done', 'tool', 'tool_result']);
    expect(value(lines[2])).to.include({ text: '4\n', turnId: 'trn_1', seq: 110 });
    expect(value(lines[3])['data']).to.deep.equal({ callId: 'c1', exitCode: 0, status: 'completed' });
    expect(value(lines[5])['data']).to.include({ callId: 't1', status: 'completed', result: 'due Friday' });
  });

  it('a loop child\'s call reaches the owner with its arguments, then with its result', async () => {
    const world = new World(loopChild(13));
    const sink = world.open();
    await settle();
    expect(sink.ofChild().map((f) => f.type)).to.deep.equal(['TOOL_CALL_START', 'TOOL_CALL_ARGS']);
    expect(sink.ofChild()[0]).to.include({ toolCallId: 'call-search', toolCallName: 'goldendocs__search_docs' });
    world.commit(...loopChild(16).filter((e) => e.seq > 13));
    await settle();
    const result = sink.ofChild().find((f) => f.type === 'TOOL_CALL_RESULT');
    expect(result).to.include({ toolCallId: 'call-search', status: 'completed' });
    expect(String(result?.['content'])).to.contain('paid leave');
  });

  it('steps the child journaled just before it ended still reach the owner', async () => {
    const world = new World(childBase);
    const sink = world.open();
    await settle();
    world.events.push(...ENGINE_STEPS);
    world.commit(parentEvent(4, 'child.completed', { childRunId: CHILD, outcome: 'answered', resultRef: { ref: 'r', hash: `sha256:${'b'.repeat(64)}`, size: 1 }, labels: { integrity: 'internal' } }));
    await settle();
    expect(sink.ofChild().map((f) => value(f)['kind'])).to.include.members(['command', 'tool_result']);
  });

  it('a shared viewer gets none of the child\'s steps', async () => {
    const world = new World([...childBase, ...ENGINE_STEPS]);
    const sink = world.open('shared');
    await settle();
    expect(sink.ofChild()).to.deep.equal([]);
  });
});
