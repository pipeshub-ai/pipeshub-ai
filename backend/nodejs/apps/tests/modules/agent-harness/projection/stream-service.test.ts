import { expect } from 'chai';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryLiveBus } from '../../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { RunJournalReader } from '../../../../src/modules/agent-harness/application/run-journal-reader';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { SessionErasedError } from '../../../../src/modules/agent-harness/domain/errors';
import { AguiFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { FrameSink, RunStreamService } from '../../../../src/modules/agent-harness/projection/stream-service';
import { parseStreamId } from '../../../../src/modules/agent-harness/projection/stream-id';
import { GoldenJournal, liveSteps, normalizeFrames, parseSse, project, readJournal, rechunked } from '../../../contracts/support/durable';
import { testCipher } from '../support/data-protection';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { SharedCitationService } from '../../../../src/modules/agent-harness/application/shared-citations';
import { InMemoryArtifactReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-artifact-access';
import { SharedArtifactService } from '../../../../src/modules/agent-harness/application/shared-artifacts';
import { finishedConversation, indexed, leaksOf, live, liveCitation, recordCitation, webCitation, withCitations } from '../support/citations';

type Line = [level: string, message: string, meta: Record<string, unknown>];

function recording(): { logger: HarnessLogger; lines: Line[] } {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } };
}

class Sink implements FrameSink {
  body = '';
  ended = false;
  /** Frames after which the socket reports a full buffer. */
  congestAfter = Number.POSITIVE_INFINITY;
  private drain: (() => void) | null = null;
  private writes = 0;

  write(chunk: string): boolean {
    this.body += chunk;
    if (chunk.startsWith(':')) return true;
    this.writes += 1;
    return this.writes < this.congestAfter;
  }
  end(): void {
    this.ended = true;
  }
  onDrain(listener: () => void): void {
    this.drain = listener;
  }
  drained(): void {
    this.congestAfter = Number.POSITIVE_INFINITY;
    this.drain?.();
  }
  get pending(): number {
    return this.writes >= this.congestAfter ? 65_536 : 0;
  }
  frames(): AguiFrame[] {
    return parseSse(this.body).map((f) => f.frame);
  }
}

/** A run whose journal grows as the test commits, with a LiveBus the "worker" publishes to. */
class World {
  readonly journal: GoldenJournal;
  readonly visible: JournalEvent[] = [];
  readonly tail = new InMemoryJournalTail();
  readonly bus = new InMemoryLiveBus();
  readonly log = recording();
  readonly service: RunStreamService;
  readonly run: HarnessRun;
  readonly records = new InMemoryRecordReadAccess();
  readonly artifacts = new InMemoryArtifactReadAccess();
  conversationReads = 0;
  /** Journal events a Node's service was handed that it had not been handed before, summed over the Nodes. */
  eventsRead = 0;
  /** Events handed again to a Node that had them: at most its last one, a read (the reader's erasure check). */
  eventsReread = 0;
  storeReads = 0;
  /** The session was erased: every event of the run is a tombstone. */
  erased = false;
  /** A stream service over the same journal, tail and bus that has read nothing yet: another Node. */
  readonly anotherNode: (shared?: (reader: RunJournalReader) => void) => RunStreamService;

  constructor(
    scenario: string,
    pieces = 3,
    liveBus = true,
    cited: unknown[] | null = null,
    conversationCited: unknown[] | null = cited,
  ) {
    const recorded = readJournal(scenario);
    this.journal = cited === null ? rechunked(recorded, pieces) : withCitations(rechunked(recorded, pieces), cited);
    this.run = {
      runId: recorded.runId,
      orgId: 'org-1',
      sessionId: recorded.sessionId,
      actorId: 'user-1',
      input: { query: recorded.query } as HarnessRun['input'],
      createdAt: 1_000,
      updatedAt: 2_000,
    } as HarnessRun;
    const readerOf = (seen: Set<JournalEvent>) => ({
      readRun: (_org: string, runId: string, after: number, limit: number) => {
        const page = this.visible.filter((e) => e.runId === runId && e.seq > after).slice(0, limit);
        this.storeReads += 1;
        // As the sealed journal answers a tombstone (E7).
        if (this.erased && page.length > 0) return Promise.reject(new SessionErasedError());
        for (const event of page) {
          if (seen.has(event)) this.eventsReread += 1;
          else this.eventsRead += 1;
          seen.add(event);
        }
        return Promise.resolve(page);
      },
    });
    this.anotherNode = (shared) => {
      const journal = readerOf(new Set());
      const journals = new JournalService(new InMemoryPayloadStore(), testCipher());
      // The Node's one reader of run journals, as its commits and its streams share it.
      const runJournal = new RunJournalReader({ journal, journals, logger: this.log.logger });
      shared?.(runJournal);
      return new RunStreamService({
        journal,
        journals,
        runJournal,
        tail: this.tail,
        conversations: {
          finishedConversation: () => {
            this.conversationReads += 1;
            return Promise.resolve(
              conversationCited === null
                ? { _id: recorded.sessionId, title: recorded.query, messages: [] }
                : finishedConversation(recorded.sessionId, conversationCited),
            );
          },
        },
        liveBus: liveBus ? this.bus : null,
        logger: this.log.logger,
        sharedCitations: new SharedCitationService(this.records, this.log.logger),
        sharedArtifacts: new SharedArtifactService(this.artifacts, this.log.logger),
      });
    };
    this.service = this.anotherNode();
  }

  /** Makes the journal through `seq` committed; the seq of the run's last committed event. */
  store(seq: number): number {
    for (const event of this.journal.events) {
      if (event.seq <= seq && !this.visible.includes(event)) this.visible.push(event);
    }
    return Math.max(0, ...this.visible.filter((e) => e.runId === this.journal.runId).map((e) => e.seq));
  }

  /** Commits the journal through `seq` and signals the tail. */
  commit(seq: number): void {
    this.store(seq);
    this.tail.changed('org-1', this.journal.runId);
  }

  /** The worker's run: each delta after the journal head it followed, then the rest. */
  async runWorker(): Promise<void> {
    for (const recorded of this.journal.live) {
      this.commit(recorded.afterSeq);
      await settle();
      this.bus.publish(recorded.frame);
      await settle();
    }
    this.commit(Number.MAX_SAFE_INTEGER);
    await settle();
  }

  logged(event: string): Line[] {
    return this.log.lines.filter(([, message]) => message === event);
  }
}

const settle = async (): Promise<void> => {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

const legacyFrames = (scenario: string): AguiFrame[] => {
  const fs = require('fs') as typeof import('fs');
  const path = require('path') as typeof import('path');
  return parseSse(fs.readFileSync(path.join(__dirname, '..', '..', '..', 'contracts', 'golden', `${scenario}.sse`), 'utf8')).map((f) => f.frame);
};

const text = (frames: AguiFrame[], type = 'TEXT_MESSAGE_CONTENT'): string =>
  frames.filter((f) => f.type === type).map((f) => String(f['delta'])).join('');

describe('agent-harness projection: RunStreamService', () => {
  it('streams a run live to its end with the legacy frames, then ends the response', async () => {
    const world = new World('agent_quick_tool_call');
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    await world.runWorker();
    await handle.done;
    expect(sink.ended).to.equal(true);
    expect(normalizeFrames(sink.frames())).to.deep.equal(normalizeFrames(legacyFrames('agent_quick_tool_call')));
    expect(parseSse(sink.body).filter((f) => f.id?.startsWith('d:')).length, 'tokens streamed live').to.be.greaterThan(1);
    expect(world.logged('projection.subscribe')[0]?.[2]).to.include({ runId: world.run.runId, resume: 'fresh', livebus: 'redis' });
    expect(world.logged('projection.unsubscribe')[0]?.[2]).to.include({ reason: 'terminal' });
    expect(world.tail.watching).to.equal(0);
  });

  it('T2: a new message\'s first delta waits for the journal, so a consumed steering input is shown before it', async () => {
    const world = new World('agent_steered_during_tool', 3);
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    const second = world.journal.live.findIndex((d) => d.frame.modelAttempt === 2);
    for (const recorded of world.journal.live.slice(0, second)) {
      world.commit(recorded.afterSeq);
      await settle();
      world.bus.publish(recorded.frame);
      await settle();
    }
    // The consumption is committed, but the tail has not woken the stream when the next message streams.
    const consumed = world.journal.events.find((e) => e.type === 'input.consumed')!.seq;
    for (const event of world.journal.events) {
      if (event.seq <= consumed && !world.visible.includes(event)) world.visible.push(event);
    }
    for (const recorded of world.journal.live.slice(second)) world.bus.publish(recorded.frame);
    await settle();
    const frames = sink.frames();
    const input = frames.findIndex((f) => f.type === 'CUSTOM' && f['name'] === 'user_input');
    const starts = frames.map((f, i) => (f.type === 'TEXT_MESSAGE_START' ? i : -1)).filter((i) => i >= 0);
    expect(input).to.be.greaterThan(-1);
    expect(starts[starts.length - 1]).to.be.greaterThan(input);
    world.commit(Number.MAX_SAFE_INTEGER);
    await handle.done;
    expect(normalizeFrames(sink.frames())).to.deep.equal(
      normalizeFrames(project(world.journal, liveSteps(world.journal)).map((f) => f.frame)),
    );
  });

  it('L-1: a reconnect mid-message with Last-Event-ID continues with no gap and no duplicate', async () => {
    const world = new World('agent_quick_tool_call', 4);
    const first = new Sink();
    const tab = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, first);
    // The tab closes two deltas into the answer's message (turn 1 only announced its tool's arguments).
    const live = world.journal.live;
    const cut = live.findIndex((d) => d.frame.kind === 'text_delta') + 2;
    world.commit(live[cut - 1]!.afterSeq);
    await settle();
    for (const d of live.slice(0, cut)) world.bus.publish(d.frame);
    await settle();
    tab.close();
    const received = parseSse(first.body);
    const lastId = received[received.length - 1]!.id!;
    expect(lastId.startsWith('d:')).to.equal(true);

    for (const d of live.slice(cut)) world.bus.publish(d.frame);
    const second = new Sink();
    const resumed = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query, lastEventId: lastId }, second);
    await settle();
    world.commit(Number.MAX_SAFE_INTEGER);
    await resumed.done;
    const all = [...received.map((f) => f.frame), ...second.frames()];
    expect(text(all)).to.equal('You get 25 days of paid leave a year.');
    expect(normalizeFrames(all)).to.deep.equal(normalizeFrames(legacyFrames('agent_quick_tool_call')));
    expect(world.logged('projection.subscribe')[1]?.[2]).to.include({ resume: 'delta' });
  });

  describe('QA22-9: a reconnect reads the journal after its cursor, not the run (PERF-B, QA22-F3)', () => {
    /** A tab that followed the first half of the run live and then dropped; `cursor` is its last frame's id. */
    const droppedTab = async () => {
      const world = new World('agent_quick_tool_call', 4);
      const first = new Sink();
      const request = { run: world.run, audience: 'owner' as const, title: world.journal.query };
      const tab = world.service.open(request, first);
      const live = world.journal.live;
      const cut = Math.floor(live.length / 2);
      for (const d of live.slice(0, cut)) {
        world.commit(d.afterSeq);
        await settle();
        world.bus.publish(d.frame);
        await settle();
      }
      tab.close();
      const received = parseSse(first.body);
      const cursor = received.at(-1)!.id!;
      const finish = async (): Promise<void> => {
        for (const d of live.slice(cut)) {
          world.commit(d.afterSeq);
          await settle();
          world.bus.publish(d.frame);
          await settle();
        }
        world.commit(Number.MAX_SAFE_INTEGER);
      };
      const runEvents = world.journal.events.filter((e) => e.runId === world.run.runId);
      const afterCursor = runEvents.filter((e) => e.seq > parseStreamId(cursor)!.position.seq).length;
      return { world, request: { ...request, lastEventId: cursor }, received, finish, runEvents: runEvents.length, afterCursor };
    };

    it('PERFB-16: on the Node that served the run, a reconnect reads at most the events after its cursor; a finished run costs it none', async () => {
      const { world, request, received, finish, runEvents, afterCursor } = await droppedTab();
      expect(afterCursor).to.be.greaterThan(0).and.lessThan(runEvents);
      const readBefore = world.eventsRead;

      world.eventsRead = 0;
      const second = new Sink();
      const resumed = world.service.open(request, second);
      await finish();
      await resumed.done;

      expect(world.eventsRead, 'events read by the resumed stream').to.be.at.most(afterCursor);
      expect(world.eventsRead, 'every event read once').to.equal(runEvents - readBefore);
      expect(world.eventsReread, 'and the last one held again, a read').to.be.at.most(world.storeReads);
      const all = [...received.map((f) => f.frame), ...second.frames()];
      expect(normalizeFrames(all)).to.deep.equal(normalizeFrames(legacyFrames('agent_quick_tool_call')));

      world.eventsRead = 0;
      const again = new Sink();
      await world.service.open(request, again).done;
      expect(world.eventsRead, 'events read resuming a finished run').to.equal(0);
      expect(normalizeFrames(again.frames())).to.deep.equal(normalizeFrames(second.frames()));
    });

    it('PERFB-17: a Node that never read the run reads it once, and answers a reconnect with the same frames', async () => {
      const { world, request, finish, runEvents } = await droppedTab();
      await finish();
      const warm = new Sink();
      await world.service.open(request, warm).done;

      world.eventsRead = 0;
      const elsewhere = world.anotherNode();
      const cold = new Sink();
      await elsewhere.open(request, cold).done;

      expect(world.eventsRead, 'the run, once').to.equal(runEvents);
      expect(normalizeFrames(cold.frames())).to.deep.equal(normalizeFrames(warm.frames()));

      world.eventsRead = 0;
      await elsewhere.open(request, new Sink()).done;
      expect(world.eventsRead, 'a second reconnect there').to.equal(0);
    });

    it('PERFB-15: a run erased after this Node read it is not replayed from what the Node held', async () => {
      const { world, request, finish } = await droppedTab();
      await finish();
      const before = new Sink();
      await world.service.open(request, before).done;
      expect(text(before.frames())).to.not.equal('');

      world.erased = true;
      const after = new Sink();
      await world.service.open(request, after).done;
      const fresh = new Sink();
      await world.service.open({ ...request, lastEventId: undefined }, fresh).done;

      expect(after.frames(), 'resuming').to.deep.equal([]);
      expect(fresh.frames(), 'from the start').to.deep.equal([]);
      expect(after.ended && fresh.ended).to.equal(true);
      expect(world.logged('projection.failed').map(([, , meta]) => meta['errorType'])).to.deep.equal([
        'SessionErasedError',
        'SessionErasedError',
      ]);
    });

    it('PERFB-18: several subscribers of one run read each journal event from the store once between them', async () => {
      const world = new World('agent_quick_tool_call', 4);
      const request = { run: world.run, audience: 'owner' as const, title: world.journal.query };
      const sinks = [new Sink(), new Sink(), new Sink()];
      const tabs = sinks.map((sink) => world.service.open(request, sink));
      await world.runWorker();
      await Promise.all(tabs.map((tab) => tab.done));

      expect(world.eventsRead).to.equal(world.journal.events.filter((e) => e.runId === world.run.runId).length);
      for (const sink of sinks) {
        expect(normalizeFrames(sink.frames())).to.deep.equal(normalizeFrames(legacyFrames('agent_quick_tool_call')));
      }
    });
  });

  describe('PERF-D: a stream reads nothing for a commit this Node has read (PERFC-F1)', () => {
    /** A stream on a Node whose reader the test also reads through, as that Node's commits do. */
    const following = async (): Promise<{ world: World; reader: RunJournalReader; sink: Sink; handle: { done: Promise<void>; close(): void } }> => {
      const world = new World('agent_quick_tool_call', 3, false);
      let reader!: RunJournalReader;
      const service = world.anotherNode((shared) => {
        reader = shared;
      });
      const sink = new Sink();
      const handle = service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
      await settle();
      return { world, reader, sink, handle };
    };
    const half = (world: World): number => world.journal.events[Math.floor(world.journal.events.length / 2)]!.seq;

    it('PERFD-12: what the run\'s commit read reaches the stream as that read answers, and the tail\'s signal for it reads nothing', async () => {
      const { world, reader, sink, handle } = await following();
      const committed = world.store(half(world));
      const before = world.storeReads;

      // The commit's own read (its receipt), then the tail's signal for each event it appended.
      await reader.read('org-1', world.run.runId, 0, committed);
      await settle();
      const projected = sink.frames().length;
      expect(projected, 'the stream projected what the commit read').to.be.greaterThan(1);
      for (let seq = 1; seq <= committed; seq += 1) world.tail.changed('org-1', world.run.runId, seq);
      await settle();

      expect(world.storeReads - before, 'one store read: the commit\'s').to.equal(1);
      expect(sink.frames().length).to.equal(projected);

      const end = world.store(Number.MAX_SAFE_INTEGER);
      await reader.read('org-1', world.run.runId, 0, end);
      await handle.done;
      expect(normalizeFrames(sink.frames())).to.deep.equal(normalizeFrames(legacyFrames('agent_quick_tool_call')));
      expect(world.eventsRead, 'each event handed to the Node once').to.equal(
        world.journal.events.filter((e) => e.runId === world.run.runId).length,
      );
      expect(reader.following, 'the stream ended').to.equal(0);
    });

    it('PERFD-12: a signal that names a seq the stream does not have, or names none, reads the journal', async () => {
      const { world, sink, handle } = await following();
      const committed = world.store(half(world));
      let before = world.storeReads;
      world.tail.changed('org-1', world.run.runId, committed);
      await settle();
      expect(world.storeReads - before, 'another Node committed: the signal is all this stream has').to.equal(1);
      const projected = sink.frames().length;
      expect(projected).to.be.greaterThan(1);

      before = world.storeReads;
      world.tail.changed('org-1', world.run.runId);
      await settle();
      expect(world.storeReads - before, 'a safety poll says only that the journal may have grown').to.equal(1);
      expect(sink.frames().length).to.equal(projected);

      world.commit(Number.MAX_SAFE_INTEGER);
      await handle.done;
      expect(normalizeFrames(sink.frames())).to.deep.equal(normalizeFrames(legacyFrames('agent_quick_tool_call')));
    });

    it('PERFD-12: a closed stream is handed nothing more and leaves no follower behind', async () => {
      const { world, reader, sink, handle } = await following();
      expect(reader.following).to.equal(1);
      handle.close();
      expect(reader.following).to.equal(0);
      const frames = sink.frames().length;
      await reader.read('org-1', world.run.runId, 0, world.store(half(world)));
      await settle();
      expect(sink.frames().length).to.equal(frames);
    });

    it('PERFD-12: a stream of an erased run is handed nothing: the read that would bring it is refused', async () => {
      const { world, reader, sink } = await following();
      const committed = world.store(half(world));
      await reader.read('org-1', world.run.runId, 0, committed);
      await settle();
      const frames = sink.frames().length;

      world.erased = true;
      world.store(Number.MAX_SAFE_INTEGER);
      const refused = await reader.read('org-1', world.run.runId).then(
        () => null,
        (error: unknown) => error,
      );
      await settle();
      expect(refused).to.be.instanceOf(SessionErasedError);
      expect(sink.frames().length).to.equal(frames);
    });
  });

  it('L-2: when the buffer expired, the reconnect ends the message and restarts it from the journal (logged)', async () => {
    const world = new World('agent_quick_tool_call', 4);
    const first = new Sink();
    const tab = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, first);
    world.commit(world.journal.live[0]!.afterSeq);
    await settle();
    world.bus.publish(world.journal.live[0]!.frame);
    await settle();
    tab.close();
    const lastId = parseSse(first.body).at(-1)!.id!;
    world.bus.expire(world.run.runId);
    const second = new Sink();
    const resumed = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query, lastEventId: lastId }, second);
    await settle();
    world.commit(Number.MAX_SAFE_INTEGER);
    await resumed.done;
    const frames = second.frames();
    expect(frames[0]).to.deep.include({ type: 'TEXT_MESSAGE_END' });
    expect(text(frames)).to.equal('You get 25 days of paid leave a year.');
    const [, , meta] = world.logged('projection.buffer_expired')[0]!;
    expect(meta).to.include({ runId: world.run.runId, choice: 'restart_message_from_journal' });
  });

  it('L-3: frames from an older fence are dropped, logged once per fence', async () => {
    const world = new World('agent_quick_tool_call');
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    world.commit(world.journal.live[0]!.afterSeq);
    await settle();
    for (let n = 1; n <= 3; n += 1) {
      world.bus.publish({ ...world.journal.live[0]!.frame, fence: 0, n, data: { text: 'zombie' } });
    }
    await settle();
    await world.runWorker();
    await handle.done;
    expect(sink.body).to.not.include('zombie');
    const lines = world.logged('projection.fence_dropped');
    expect(lines).to.have.length(1);
    expect(lines[0]![2]).to.include({ staleFence: 0, currentFence: 1, runId: world.run.runId });
  });

  it('a delta lost on the bus is logged as a gap; the stream still ends with the journal\'s answer', async () => {
    const world = new World('agent_quick_tool_call');
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    const first = world.journal.live[0]!;
    world.commit(first.afterSeq);
    await settle();
    world.bus.publish({ ...first.frame, n: first.frame.n + 1, data: { text: 'skipped-ahead' } });
    await settle();
    world.commit(Number.MAX_SAFE_INTEGER);
    await handle.done;
    expect(sink.body).to.not.include('skipped-ahead');
    const lines = world.logged('projection.delta_gap');
    expect(lines).to.have.length(1);
    expect(lines[0]![2]).to.include({
      runId: world.run.runId,
      fence: first.frame.fence,
      modelAttempt: first.frame.modelAttempt,
      expected: first.frame.n,
      got: first.frame.n + 1,
    });
    expect(text(sink.frames())).to.equal('You get 25 days of paid leave a year.');
  });

  it('L-4: a client that cannot keep up is resynced from the journal and the worker is never blocked', async () => {
    const world = new World('agent_quick_tool_call', 6);
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    world.commit(world.journal.live[0]!.afterSeq);
    await settle();
    sink.congestAfter = sink.frames().length + 3;
    for (const d of world.journal.live) world.bus.publish(d.frame);
    await settle();
    sink.drained();
    world.commit(Number.MAX_SAFE_INTEGER);
    await handle.done;
    const frames = sink.frames();
    const starts = frames.filter((f) => f.type === 'TEXT_MESSAGE_START' && f['messageId']);
    const answerId = starts[starts.length - 1]!['messageId'];
    const answer = frames.filter((f) => f.type === 'TEXT_MESSAGE_CONTENT' && f['messageId'] === answerId).map((f) => f['delta']).join('');
    expect(answer).to.equal('You get 25 days of paid leave a year.');
    const [level, , meta] = world.logged('projection.overflow_resync')[0]!;
    expect(level).to.equal('warn');
    expect(meta).to.include({ runId: world.run.runId, choice: 'restart_message_from_journal' });
    expect(frames.at(-1)!.type).to.equal('RUN_FINISHED');
  });

  it('L-5: with Redis down the stream falls back to the journal, message by message, and completes (logged)', async () => {
    const world = new World('agent_quick_tool_call');
    world.bus.failing = new Error('ECONNREFUSED');
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    await world.runWorker();
    await handle.done;
    expect(parseSse(sink.body).every((f) => !f.id?.startsWith('d:'))).to.equal(true);
    expect(normalizeFrames(sink.frames())).to.deep.equal(normalizeFrames(legacyFrames('agent_quick_tool_call')));
    const [level, , meta] = world.logged('projection.livebus_unavailable')[0]!;
    expect(level).to.equal('warn');
    expect(meta).to.include({ runId: world.run.runId, choice: 'journal_only' });
  });

  it('L-9: two tabs on the same run get the same frames; closing one changes nothing for the other', async () => {
    const world = new World('agent_react_tool_error_then_success');
    const a = new Sink();
    const b = new Sink();
    const tabA = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, a);
    const tabB = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, b);
    world.commit(world.journal.live[0]!.afterSeq);
    await settle();
    tabA.close();
    await world.runWorker();
    await tabB.done;
    const before = parseSse(a.body);
    expect(parseSse(b.body).slice(0, before.length)).to.deep.equal(before);
    expect(b.frames().at(-1)!.type).to.equal('RUN_FINISHED');
    expect(world.logged('projection.unsubscribe').map(([, , meta]) => meta['reason'])).to.deep.equal(['client_closed', 'terminal']);
  });

  it('L-14: subscribing after the run finished replays it whole from the journal', async () => {
    const world = new World('agent_cancel_mid_stream');
    world.commit(Number.MAX_SAFE_INTEGER);
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    await handle.done;
    expect(normalizeFrames(sink.frames())).to.deep.equal(normalizeFrames(legacyFrames('agent_cancel_mid_stream')));
  });

  it('SF-7: a surface that disconnects mid-run stops nothing; its reconnect from the last id gets exactly the rest', async () => {
    const whole = new World('agent_quick_tool_call', 4);
    const full = new Sink();
    const all = whole.service.open({ run: whole.run, audience: 'external', title: whole.journal.query }, full);
    await settle();
    whole.commit(Number.MAX_SAFE_INTEGER);
    await all.done;

    const world = new World('agent_quick_tool_call', 4);
    const first = new Sink();
    const surface = world.service.open({ run: world.run, audience: 'external', title: world.journal.query }, first);
    const outcome = world.journal.events.find((e) => e.type === 'tool.outcome')!.seq;
    world.commit(outcome);
    await settle();
    surface.close('client_closed');
    await surface.done;
    // The run goes on without a subscriber: the worker commits the rest.
    world.commit(Number.MAX_SAFE_INTEGER);
    const lastId = parseSse(first.body).at(-1)!.id!;
    const second = new Sink();
    const again = world.service.open({ run: world.run, audience: 'external', title: world.journal.query, lastEventId: lastId }, second);
    await again.done;
    // RUN_FINISHED carries the wall clock of the subscription that sent it.
    const unclocked = (body: string) =>
      parseSse(body).map((f) => JSON.parse(JSON.stringify(f).replace(/"timestamp":"[^"]+"/, '"timestamp":"<clock>"')) as unknown);
    expect([...unclocked(first.body), ...unclocked(second.body)]).to.deep.equal(unclocked(full.body));
    expect(parseSse(second.body).filter((f) => f.frame.type === 'RUN_FINISHED')).to.have.length(1);
    expect(world.logged('projection.unsubscribe').map(([, , meta]) => [meta['reason'], meta['audience']])).to.deep.equal([
      ['client_closed', 'external'],
      ['terminal', 'external'],
    ]);
    expect(world.conversationReads, 'no citation check for the owner').to.equal(1);
  });

  it('L-7: a shared viewer\'s stream carries no tool arguments, results or narration', async () => {
    const world = new World('agent_artifact_marker_answer');
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'shared' }, sink);
    await world.runWorker();
    await handle.done;
    expect(sink.body).to.not.include('TOOL_CALL');
    expect(sink.body).to.not.include('TEXT_MESSAGE');
    expect(sink.body).to.not.include('art-q3\\"');
    expect(sink.body).to.include('/artifacts/-');
    // SS0: without the viewer's credential no artifact can be checked, so it is a placeholder.
    expect(sink.body).to.not.include('record:art-q3');
    expect(sink.body).to.include('::artifact_unavailable');
  });
  describe('C-6: one citation check per shared subscription, with the viewer\'s credential', () => {
    const cited = [recordCitation('rec-open', 1), recordCitation('rec-closed', 2), webCitation(3)];
    const shared = (world: World, extra: Record<string, unknown> = {}) => ({
      run: world.run,
      audience: 'shared' as const,
      authorization: 'Bearer viewer',
      requestId: 'req-share',
      ...extra,
    });

    it('a live shared stream checks once, before its end, and hides what the viewer cannot read', async () => {
      const world = new World('agent_quick_tool_call', 3, true, cited);
      world.records.grant('Bearer viewer', 'rec-open');
      const sink = new Sink();
      const handle = world.service.open(shared(world), sink);
      await world.runWorker();
      await handle.done;
      expect(world.records.queries).to.deep.equal([{ authorization: 'Bearer viewer', sources: [indexed('rec-open'), indexed('rec-closed')] }]);
      expect(sink.body).to.include('NAME-rec-open');
      expect(sink.body).to.include('example.test/page');
      for (const leak of leaksOf('rec-closed')) expect(sink.body, leak).to.not.include(leak);
      expect(sink.frames().at(-1)!.type).to.equal('RUN_FINISHED');
      expect(world.conversationReads, 'the conversation read for the check is the one RUN_FINISHED carries').to.equal(1);
      const lines = world.logged('citations.access_checked');
      expect(lines).to.have.length(1);
      expect(lines[0]![0]).to.equal('info');
      expect(lines[0]![2]).to.include({
        allowed: 1,
        denied: 1,
        orgId: 'org-1',
        sessionId: world.run.sessionId,
        runId: world.run.runId,
        requestId: 'req-share',
        surface: 'stream',
      });
      expect(JSON.stringify(world.log.lines), 'no denied record id in any log line').to.not.include('rec-closed');
    });

    it('C-18: a live citation is shown through its readable indexed copy; without one it is hidden as unverifiable', async () => {
      const world = new World('agent_quick_tool_call', 3, true, [liveCitation('f-copy', 1), liveCitation('f-none', 2), webCitation(3)]);
      world.records.grant('Bearer viewer', 'copy-1').indexCopy('DRIVE', 'f-copy', 'copy-1');
      world.commit(Number.MAX_SAFE_INTEGER);
      const sink = new Sink();
      await world.service.open(shared(world), sink).done;
      expect(world.records.queries).to.deep.equal([{ authorization: 'Bearer viewer', sources: [live('f-copy'), live('f-none')] }]);
      expect(sink.body).to.include('NAME-f-copy');
      for (const leak of [...leaksOf('f-none'), 'TEMP-f-none']) expect(sink.body, leak).to.not.include(leak);
      const [, , meta] = world.logged('citations.access_checked')[0]!;
      expect(meta).to.include({ allowed: 1, denied: 0, unverifiable: 1, records: 2 });
      expect(meta).to.include({ liveAllowed: 1, liveDenied: 0, liveUnverifiable: 1 });
      expect(meta).to.not.have.property('indexedAllowed');
      expect(JSON.stringify(world.log.lines)).to.not.include('f-none');
    });

    it('the check covers what the finished conversation cites beyond the journal', async () => {
      const world = new World('agent_quick_tool_call', 3, true, [recordCitation('rec-open', 1)], [
        recordCitation('rec-open', 1),
        liveCitation('f-copy', 2),
      ]);
      world.records.grant('Bearer viewer', 'rec-open', 'copy-1').indexCopy('DRIVE', 'f-copy', 'copy-1');
      world.commit(Number.MAX_SAFE_INTEGER);
      const sink = new Sink();
      await world.service.open(shared(world), sink).done;
      expect(world.records.queries).to.deep.equal([{ authorization: 'Bearer viewer', sources: [indexed('rec-open'), live('f-copy')] }]);
      expect(sink.body).to.include('NAME-f-copy');
    });

    it('a reconnect is a new subscription: it checks again and sees access revoked since', async () => {
      const world = new World('agent_quick_tool_call', 3, true, cited);
      world.records.grant('Bearer viewer', 'rec-open', 'rec-closed');
      world.commit(Number.MAX_SAFE_INTEGER);
      const first = new Sink();
      await world.service.open(shared(world), first).done;
      expect(first.body).to.include('NAME-rec-closed');
      const cut = parseSse(first.body)[2]!.id!;
      world.records.revoke('Bearer viewer', 'rec-closed');
      const second = new Sink();
      await world.service.open(shared(world, { lastEventId: cut }), second).done;
      expect(world.records.queries).to.have.length(2);
      for (const leak of leaksOf('rec-closed')) expect(second.body, leak).to.not.include(leak);
      expect(second.body).to.include('NAME-rec-open');
    });

    it('fails closed: when the check fails every record citation is hidden, the failure logged, and the stream still ends', async () => {
      const world = new World('agent_quick_tool_call', 3, true, cited);
      world.records.grant('Bearer viewer', 'rec-open', 'rec-closed');
      world.records.failWith = 'unavailable';
      world.commit(Number.MAX_SAFE_INTEGER);
      const sink = new Sink();
      await world.service.open(shared(world), sink).done;
      for (const leak of [...leaksOf('rec-open'), ...leaksOf('rec-closed')]) expect(sink.body, leak).to.not.include(leak);
      expect(sink.frames().at(-1)!.type).to.equal('RUN_FINISHED');
      const [level, , meta] = world.logged('citations.access_check_failed')[0]!;
      expect(level).to.equal('warn');
      expect(meta).to.include({ reason: 'unavailable', runId: world.run.runId, requestId: 'req-share', choice: 'hide_record_citations' });
    });

    it('a shared stream without the viewer\'s credential asks nothing and hides record citations', async () => {
      const world = new World('agent_quick_tool_call', 3, true, cited);
      world.commit(Number.MAX_SAFE_INTEGER);
      const sink = new Sink();
      await world.service.open(shared(world, { authorization: undefined }), sink).done;
      expect(world.records.queries).to.deep.equal([]);
      expect(sink.body).to.not.include('NAME-rec-open');
      expect(world.logged('citations.access_check_failed')[0]![2]).to.include({ reason: 'no_credential' });
    });

    it('the owner\'s stream makes no check and keeps every citation', async () => {
      const world = new World('agent_quick_tool_call', 3, true, cited);
      const sink = new Sink();
      const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query, authorization: 'Bearer owner' }, sink);
      await world.runWorker();
      await handle.done;
      expect(world.records.queries).to.deep.equal([]);
      for (const leak of leaksOf('rec-closed')) expect(sink.body).to.include(leak);
      expect(world.logged('citations.access_checked')).to.deep.equal([]);
    });
  });

  describe('SS0: artifacts reach a shared viewer only when their sources are readable to them', () => {
    const shared = (world: World) => ({
      run: world.run,
      audience: 'shared' as const,
      authorization: 'Bearer viewer',
      requestId: 'req-share',
    });
    const LEAKS = ['art-q3', 'q3-revenue.csv', 'doc-q3'];
    const cards = (sink: Sink): unknown[] =>
      sink
        .frames()
        .filter((f) => f.type === 'STATE_DELTA')
        .flatMap((f) => f['delta'] as Array<{ path: string; value: unknown }>)
        .filter((op) => op.path === '/artifacts/-')
        .map((op) => op.value);

    it('AN-6: an artifact whose provenance is unknown is a placeholder card and marker; one check per subscription', async () => {
      const world = new World('agent_artifact_marker_answer');
      const sink = new Sink();
      const handle = world.service.open(shared(world), sink);
      await world.runWorker();
      await handle.done;
      expect(world.artifacts.queries).to.deep.equal([{ authorization: 'Bearer viewer', artifactIds: ['art-q3'] }]);
      expect(cards(sink)).to.deep.equal([{ unavailable: true }]);
      expect(sink.body).to.include('::artifact_unavailable');
      for (const leak of LEAKS) expect(sink.body, leak).to.not.include(leak);
      expect(sink.frames().at(-1)!.type).to.equal('RUN_FINISHED');
      const [level, , meta] = world.logged('artifacts.access_checked')[0]!;
      expect(level).to.equal('info');
      expect(meta).to.include({ artifacts: 1, allowed: 0, unverifiable: 1, runId: world.run.runId, surface: 'stream' });
      expect(JSON.stringify(world.log.lines)).to.not.include('art-q3');
    });

    it('a readable artifact keeps its card and its marker', async () => {
      const world = new World('agent_artifact_marker_answer');
      world.artifacts.grant('Bearer viewer', 'art-q3');
      world.commit(Number.MAX_SAFE_INTEGER);
      const sink = new Sink();
      await world.service.open(shared(world), sink).done;
      expect(cards(sink)).to.have.length(1);
      expect(cards(sink)[0]).to.include({ artifactId: 'art-q3', fileName: 'q3-revenue.csv' });
      expect(sink.body).to.include('::artifact[q3-revenue.csv](record:art-q3)');
      expect(sink.body).to.not.include('::artifact_unavailable');
    });

    it('fails closed: a failed check hides the artifact and is logged', async () => {
      const world = new World('agent_artifact_marker_answer');
      world.artifacts.grant('Bearer viewer', 'art-q3');
      world.artifacts.failWith = 'unavailable';
      world.commit(Number.MAX_SAFE_INTEGER);
      const sink = new Sink();
      await world.service.open(shared(world), sink).done;
      for (const leak of LEAKS) expect(sink.body, leak).to.not.include(leak);
      expect(world.logged('artifacts.access_check_failed')[0]![2]).to.include({ reason: 'unavailable', choice: 'hide_artifacts' });
    });

    it('the owner\'s stream makes no check and keeps the artifact', async () => {
      const world = new World('agent_artifact_marker_answer');
      world.commit(Number.MAX_SAFE_INTEGER);
      const sink = new Sink();
      await world.service.open({ run: world.run, audience: 'owner', title: world.journal.query, authorization: 'Bearer owner' }, sink).done;
      expect(world.artifacts.queries).to.deep.equal([]);
      expect(sink.body).to.include('q3-revenue.csv');
      expect(sink.body).to.not.include('::artifact_unavailable');
    });
  });
  it('WP115-FU-2: an ask a held grant covers that Node does not answer is drawn when its grace ends', async function () {
    this.timeout(8_000);
    const world = new World('agent_quick_tool_call');
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', title: world.journal.query }, sink);
    world.commit(12);
    await settle();
    const last = world.visible[world.visible.length - 1] as JournalEvent;
    const ts = new Date().toISOString();
    const key = `sha256:${'2'.repeat(64)}`;
    const toolId = 'slack@default/send_message';
    const at = (seq: number, type: string, writer: 'worker' | 'node', data: Record<string, unknown>): JournalEvent =>
      ({ ...last, seq, type, writer, critical: true, data, ts });
    world.visible.push(
      at(13, 'interaction.granted', 'node', { interactionId: 'ix.approve.t0.0', scope: 'conversation', toolId, key, sensitivity: 'private', fromRunId: 'run-sibling' }),
      at(14, 'interaction.explained', 'worker', { interactionId: 'ix.approve.t1.0', rule: 'flow_external_write', toolId, sensitivity: 'private', destination: { label: '#sales', key }, conversationGrant: true }),
      at(15, 'interaction.requested', 'worker', { interactionId: 'ix.approve.t1.0', kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
    );
    world.tail.changed('org-1', world.journal.runId);
    await settle();
    const cards = (): AguiFrame[] => sink.frames().filter((f) => f['name'] === 'approval_requested');
    expect(cards()).to.deep.equal([]);
    await new Promise((resolve) => setTimeout(resolve, 3_200));
    await settle();
    expect(cards().map((f) => (f['value'] as Record<string, unknown>)['interactionId'])).to.deep.equal(['ix.approve.t1.0']);
    handle.close();
  });
});
