/** PERF-B (QA22-F3, QA22-F4): a run's journal is read from the store once per Node. */
import { expect } from 'chai';
import {
  InMemoryJournalStore,
  InMemoryPayloadStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunJournalReader } from '../../../src/modules/agent-harness/application/run-journal-reader';
import {
  JournalPayloadMissingError,
  SessionErasedError,
} from '../../../src/modules/agent-harness/domain/errors';
import { JournalEvent, JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import { testCipher } from './support/data-protection';
import { bulkyOutcome, position, turnStarted, workerEvent } from './support/journal-fixtures';

const ORG = 'org-1';
const INLINE_MAX = 512;
const CANARY = 'forty-two days of leave';

const outcome = (invocationId: string, receipt: Record<string, unknown>): JournalEventInput =>
  workerEvent('tool.outcome', { invocationId, status: 'ok', receipt, labels: { integrity: 'external' } });

const settle = async (): Promise<void> => {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

const rejection = async (work: Promise<unknown>): Promise<unknown> =>
  work.then(
    () => expect.fail('expected a rejection'),
    (error: unknown) => error,
  );

/** A journal store that counts what a reader asks of it, and can hold or fail a read. */
class World {
  readonly store = new InMemoryJournalStore();
  readonly payloads = new InMemoryPayloadStore();
  readonly journals = new JournalService(this.payloads, testCipher(), INLINE_MAX);
  now = 1_000;
  /** `[runId, afterSeq, events handed]` per store read. */
  readonly reads: Array<[runId: string, afterSeq: number, handed: number]> = [];
  payloadReads = 0;
  /** Events the reader took from the store as new to it (it resolves those, and only those), per run. */
  readonly taken = new Map<string, number>();
  /** Runs whose session was erased: the store refuses their tombstones, as the sealed journal does. */
  readonly erased = new Set<string>();
  failNextRead: Error | null = null;
  /** While set, store reads wait for it. */
  gate: Promise<void> | null = null;
  /** While set, a store read that has taken its page waits for it before answering. */
  answerGate: Promise<void> | null = null;
  readonly logs: Array<[level: string, message: string, meta: Record<string, unknown>]> = [];

  reader(options: { maxBytes?: number; idleMs?: number; pageSize?: number } = {}): RunJournalReader {
    const at =
      (level: string) =>
      (message: string, meta?: unknown): void => {
        this.logs.push([level, message, (meta ?? {}) as Record<string, unknown>]);
      };
    const logger: HarnessLogger = { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') };
    return new RunJournalReader({
      journal: {
        readRun: async (orgId, runId, afterSeq, limit) => {
          if (this.gate !== null) await this.gate;
          if (this.failNextRead !== null) {
            const error = this.failNextRead;
            this.failNextRead = null;
            throw error;
          }
          const page = await this.store.readRun(orgId, runId, afterSeq, limit);
          this.reads.push([runId, afterSeq, page.length]);
          if (this.answerGate !== null) await this.answerGate;
          if (page.length > 0 && this.erased.has(runId)) throw new SessionErasedError();
          return page;
        },
      },
      journals: {
        resolve: (events) => {
          this.payloadReads += events.filter((event) => event.payloadRef !== undefined).length;
          for (const event of events) this.taken.set(event.runId ?? '', (this.taken.get(event.runId ?? '') ?? 0) + 1);
          return this.journals.resolve(events);
        },
      },
      clock: { now: () => this.now },
      logger,
      ...options,
    });
  }

  async append(runId: string, events: JournalEventInput[]): Promise<void> {
    await this.store.append(await this.journals.prepare({ ...position({ runId }), writer: 'worker', events }));
  }

  /** What a reader holding nothing gets: the run read and resolved whole. */
  async whole(runId: string): Promise<JournalEvent[]> {
    return this.journals.resolve(await this.store.readRun(ORG, runId, 0, 10_000));
  }

  /** Events of the run the reader took as new, over all its reads. */
  took(runId = 'run-1'): number {
    return this.taken.get(runId) ?? 0;
  }
}

describe('RunJournalReader', () => {
  it('PERFB-12: a read asks the store only for the events after the last one it holds', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1), bulkyOutcome(INLINE_MAX * 4)]);

    const first = await reader.read(ORG, 'run-1');
    expect(first).to.deep.equal(await w.whole('run-1'));
    expect(first.map((event) => event.seq)).to.deep.equal([1, 2]);
    expect(first[1]!.data?.['receipt'], 'the claim-checked payload, resolved').to.deep.equal({ body: 'x'.repeat(INLINE_MAX * 4) });

    expect(w.took()).to.equal(2);

    w.reads.length = 0;
    w.payloadReads = 0;
    await w.append('run-1', [turnStarted(2)]);
    const second = await reader.read(ORG, 'run-1');

    expect(second).to.deep.equal(await w.whole('run-1'));
    expect(w.reads, 'one read, from the last event held (seq 2) on').to.deep.equal([['run-1', 1, 2]]);
    expect(w.took(), 'the one new event').to.equal(3);
    expect(w.payloadReads, 'the payload already resolved is not fetched again').to.equal(0);

    w.reads.length = 0;
    expect(await reader.read(ORG, 'run-1')).to.deep.equal(second);
    expect(w.reads, 'nothing new: one read of the last event held').to.deep.equal([['run-1', 2, 1]]);
    expect(w.took()).to.equal(3);
  });

  it('a read from a cursor returns the events after it', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [1, 2, 3, 4, 5].map(turnStarted));
    for (const [after, seqs] of [
      [0, [1, 2, 3, 4, 5]],
      [-3, [1, 2, 3, 4, 5]],
      [1, [2, 3, 4, 5]],
      [4, [5]],
      [5, []],
      [99, []],
    ] as Array<[number, number[]]>) {
      expect((await reader.read(ORG, 'run-1', after)).map((event) => event.seq), `after ${String(after)}`).to.deep.equal(seqs);
    }
    expect(w.took(), 'the run was read once').to.equal(5);
    expect(w.reads.every(([, , handed]) => handed <= 5)).to.equal(true);
    expect(w.reads.slice(1).map(([, afterSeq, handed]) => [afterSeq, handed]), 'each later read: the last event held, nothing more').to.deep.equal(Array.from({ length: 5 }, () => [4, 1]));
  });

  it("a run sharing its stream with another run gets its own events, all of them, whatever was read when", async () => {
    const w = new World();
    const reader = w.reader({ pageSize: 2 });
    await w.append('run-1', [turnStarted(1)]);
    await w.append('run-2', [turnStarted(1), turnStarted(2)]);
    expect((await reader.read(ORG, 'run-1')).map((event) => event.seq)).to.deep.equal([1]);
    await w.append('run-1', [turnStarted(2), turnStarted(3), turnStarted(4)]);
    await w.append('run-2', [turnStarted(3)]);
    await w.append('run-1', [turnStarted(5)]);

    expect((await reader.read(ORG, 'run-1')).map((event) => event.seq)).to.deep.equal([1, 4, 5, 6, 8]);
    expect(await reader.read(ORG, 'run-1')).to.deep.equal(await w.whole('run-1'));
    expect(await reader.read(ORG, 'run-2')).to.deep.equal(await w.whole('run-2'));
    expect(w.took('run-1'), 'each of its events taken once').to.equal(5);
  });

  it('PERFB-13: whatever was appended and read in between, a read equals the run read whole', async () => {
    const w = new World();
    const reader = w.reader({ pageSize: 3 });
    // A fixed interleaving of appends of 0–4 events (some claim-checked) and reads.
    const sizes = [1, 0, 4, 2, 0, 3, 3, 1, 4, 0, 2];
    let turn = 0;
    for (const [step, size] of sizes.entries()) {
      const events = Array.from({ length: size }, () =>
        (turn += 1) % 3 === 0 ? bulkyOutcome(INLINE_MAX * 2 + turn) : turnStarted(turn),
      );
      if (events.length > 0) await w.append('run-1', events);
      if (step % 2 === 1) continue;
      expect(await reader.read(ORG, 'run-1'), `after step ${String(step)}`).to.deep.equal(await w.whole('run-1'));
    }
    expect(await reader.read(ORG, 'run-1')).to.deep.equal(await w.whole('run-1'));
    expect(w.took(), 'each event taken once').to.equal(sizes.reduce((sum, size) => sum + size, 0));
  });

  it('PERFB-15: reads of one run that arrive together share a store read, and none misses what was committed before it asked', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1)]);
    let open!: () => void;
    w.gate = new Promise((resolve) => {
      open = resolve;
    });

    const inFlight = reader.read(ORG, 'run-1');
    await settle();
    // Committed while the first read is at the store: that read may or may not see it.
    await w.append('run-1', [turnStarted(2)]);
    const later = [reader.read(ORG, 'run-1'), reader.read(ORG, 'run-1', 1), reader.read(ORG, 'run-1')];
    w.gate = null;
    open();
    const [a, b, c] = await Promise.all(later);
    await inFlight;

    expect(a!.map((event) => event.seq)).to.deep.equal([1, 2]);
    expect(b!.map((event) => event.seq)).to.deep.equal([2]);
    expect(c).to.deep.equal(a);
    expect(w.reads.length, 'the read in flight and one more for the three that waited').to.equal(2);
  });

  it('PERFD-8: a caller naming a seq the read in flight began knowing of is answered by that read', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1), turnStarted(2)]);
    let open!: () => void;
    w.gate = new Promise((resolve) => {
      open = resolve;
    });

    // The commit that appended seq 2 reads ahead, then answers its receipt; a stream is told of seq 1 and of seq 2.
    reader.warm(ORG, 'run-1', 2);
    await settle();
    const sharing = [reader.read(ORG, 'run-1', 1, 2), reader.read(ORG, 'run-1', 0, 1), reader.read(ORG, 'run-1', 0, 2)];
    w.gate = null;
    open();
    const [receipt, first, second] = await Promise.all(sharing);

    expect(receipt!.map((event) => event.seq)).to.deep.equal([2]);
    expect(first).to.deep.equal(await w.whole('run-1'));
    expect(second).to.deep.equal(first);
    expect(w.reads.length, 'one store read for the read-ahead and the three it covers').to.equal(1);
  });

  it('PERFD-8: a caller naming a later seq than the read in flight knew of, or none, waits for the next read', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1)]);
    let open!: () => void;
    w.gate = new Promise((resolve) => {
      open = resolve;
    });

    // The read in flight has taken its page (seq 1 alone) and not answered yet.
    w.gate = null;
    w.answerGate = new Promise((resolve) => {
      open = resolve;
    });
    reader.warm(ORG, 'run-1', 1);
    await settle();
    expect(w.reads).to.deep.equal([['run-1', 0, 1]]);
    await w.append('run-1', [turnStarted(2)]);
    const later = reader.read(ORG, 'run-1', 0, 2);
    const unnamed = reader.read(ORG, 'run-1');
    // The next read will begin knowing of seq 2: a caller naming it shares that one.
    const covered = reader.read(ORG, 'run-1', 0, 2);
    w.answerGate = null;
    open();

    expect((await later).map((event) => event.seq)).to.deep.equal([1, 2]);
    expect(await unnamed).to.deep.equal(await w.whole('run-1'));
    expect(await covered).to.deep.equal(await w.whole('run-1'));
    expect(w.reads.length, 'the read in flight and one more').to.equal(2);
  });

  it('PERFD-8: once a read has ended, a caller naming a seq it brought is still answered by the store', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1)]);
    await reader.read(ORG, 'run-1', 0, 1);

    w.erased.add('run-1');
    expect(await rejection(reader.read(ORG, 'run-1', 0, 1))).to.be.instanceOf(SessionErasedError);
    expect(w.reads.length).to.equal(2);
  });

  it('PERFD-12: a follower is handed what a store read brought beyond its own place, and nothing without a read', async () => {
    const w = new World();
    const reader = w.reader();
    let place = 0;
    const handed: number[][] = [];
    const following = reader.follow(ORG, 'run-1', () => place, (events) => {
      handed.push(events.map((event) => event.seq));
      place = events[events.length - 1]!.seq;
    });
    const elsewhere: number[][] = [];
    reader.follow(ORG, 'run-2', () => 0, (events) => elsewhere.push(events.map((event) => event.seq)));
    reader.follow('org-2', 'run-1', () => 0, (events) => elsewhere.push(events.map((event) => event.seq)));

    await w.append('run-1', [turnStarted(1), turnStarted(2)]);
    await settle();
    expect(handed, 'a commit alone hands nothing over').to.deep.equal([]);

    await reader.read(ORG, 'run-1');
    expect(handed).to.deep.equal([[1, 2]]);
    await reader.read(ORG, 'run-1');
    expect(handed, 'a read that brought nothing new').to.deep.equal([[1, 2]]);

    await w.append('run-1', [turnStarted(3)]);
    place = 0;
    reader.warm(ORG, 'run-1', 3);
    await settle();
    expect(handed[1], 'from the follower\'s place, not from the read\'s').to.deep.equal([1, 2, 3]);

    expect(reader.following).to.equal(3);
    following.close();
    following.close();
    expect(reader.following).to.equal(2);
    await w.append('run-1', [turnStarted(4)]);
    await reader.read(ORG, 'run-1');
    expect(handed).to.have.length(2);
    expect(elsewhere, 'another run, or the same id in another org').to.deep.equal([]);
  });

  it('PERFD-12: a read that fails hands nothing over', async () => {
    const w = new World();
    const reader = w.reader();
    const handed: number[][] = [];
    reader.follow(ORG, 'run-1', () => 0, (events) => handed.push(events.map((event) => event.seq)));
    await w.append('run-1', [turnStarted(1)]);
    await reader.read(ORG, 'run-1');
    w.erased.add('run-1');
    await rejection(reader.read(ORG, 'run-1'));
    expect(handed).to.deep.equal([[1]]);
  });

  it('PERFB-15: a read that fails leaves what was held, and the next read asks only for what is missing', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1), turnStarted(2)]);
    await reader.read(ORG, 'run-1');
    await w.append('run-1', [turnStarted(3)]);
    w.reads.length = 0;

    w.failNextRead = new Error('mongo is away');
    expect(((await rejection(reader.read(ORG, 'run-1'))) as Error).message).to.equal('mongo is away');

    expect((await reader.read(ORG, 'run-1')).map((event) => event.seq)).to.deep.equal([1, 2, 3]);
    expect(w.reads, 'from the last event held on').to.deep.equal([['run-1', 1, 2]]);
    expect(w.took()).to.equal(3);
  });

  it('PERFB-15: a page whose payload cannot be resolved is not held in part', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1), bulkyOutcome(INLINE_MAX * 4), turnStarted(2)]);
    const kept = new Map(w.payloads.objects);
    w.payloads.objects.clear();

    expect(await rejection(reader.read(ORG, 'run-1'))).to.be.instanceOf(JournalPayloadMissingError);

    for (const [ref, bytes] of kept) w.payloads.objects.set(ref, bytes);
    expect(await reader.read(ORG, 'run-1')).to.deep.equal(await w.whole('run-1'));
  });

  it('PERFB-15: over the byte budget the least recently read run is dropped; a run larger than the budget is answered and not held', async () => {
    const w = new World();
    const big = (label: string): JournalEventInput => outcome(label, { body: 'y'.repeat(4_000) });
    for (const runId of ['run-a', 'run-b', 'run-c']) await w.append(runId, [big(runId)]);
    // Each run weighs 8,000 bytes of body (two a character) and under 2,000 of envelope: room for two, not three.
    const reader = w.reader({ maxBytes: 20_000 });

    await reader.read(ORG, 'run-a');
    await reader.read(ORG, 'run-b');
    await reader.read(ORG, 'run-a');
    await reader.read(ORG, 'run-c');
    expect([w.took('run-a'), w.took('run-b'), w.took('run-c')]).to.deep.equal([1, 1, 1]);

    await reader.read(ORG, 'run-a');
    await reader.read(ORG, 'run-c');
    expect([w.took('run-a'), w.took('run-c')], 'run-a and run-c were kept').to.deep.equal([1, 1]);
    expect(await reader.read(ORG, 'run-b')).to.deep.equal(await w.whole('run-b'));
    expect(w.took('run-b'), 'run-b was dropped and is taken again').to.equal(2);

    const small = w.reader({ maxBytes: 100 });
    for (const pass of [1, 2]) {
      const before = w.took('run-a');
      expect(await small.read(ORG, 'run-a'), `pass ${String(pass)}`).to.deep.equal(await w.whole('run-a'));
      expect(w.took('run-a') - before, 'taken whole each time').to.equal(1);
    }
  });

  it('PERFB-15: a run nobody read for the idle time is dropped; one read just inside it is kept', async () => {
    const w = new World();
    const reader = w.reader({ idleMs: 1_000 });
    await w.append('run-1', [turnStarted(1)]);
    await w.append('run-2', [turnStarted(1)]);
    await reader.read(ORG, 'run-1');
    w.now += 999;
    await reader.read(ORG, 'run-2');

    w.now += 1;
    await reader.read(ORG, 'run-2');
    expect(w.took('run-2'), 'run-2 was read 1 ms ago: still held').to.equal(1);

    await reader.read(ORG, 'run-1');
    expect(w.took('run-1'), 'run-1 lapsed at exactly the idle time and is taken again').to.equal(2);
  });

  it('PERFB-15: what is held cannot be changed through an answer', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [outcome('i', { rows: [{ text: CANARY }] })]);
    const events = await reader.read(ORG, 'run-1');
    const event = events[0]!;

    expect(() => {
      event.type = 'changed';
    }).to.throw(TypeError);
    expect(() => {
      (event.data!['receipt'] as { rows: Array<{ text: string }> }).rows[0]!.text = 'changed';
    }).to.throw(TypeError);
    expect(() => {
      (event.data!['receipt'] as { rows: unknown[] }).rows.push({});
    }).to.throw(TypeError);
    events.pop();
    expect(await reader.read(ORG, 'run-1')).to.deep.equal(await w.whole('run-1'));
  });

  it('warming reads ahead off the caller path; when it fails it says so without content and the next read does the work', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [outcome('i', { text: CANARY })]);

    reader.warm(ORG, 'run-1');
    await settle();
    expect(w.took()).to.equal(1);

    await w.append('run-1', [turnStarted(2)]);
    w.failNextRead = new TypeError(`cannot read ${CANARY}`);
    reader.warm(ORG, 'run-1');
    await settle();
    const [level, , meta] = w.logs.find(([, message]) => message === 'run_journal.warm_failed')!;
    expect(level).to.equal('debug');
    expect(meta).to.include({ orgId: ORG, runId: 'run-1', errorType: 'TypeError', choice: 'read_on_demand' });
    expect(JSON.stringify(w.logs)).to.not.include(CANARY);

    expect(await reader.read(ORG, 'run-1')).to.deep.equal(await w.whole('run-1'));
    expect(w.took(), 'the read took what the failed warming did not').to.equal(2);
  });

  it('another org never sees a run by its id', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [turnStarted(1)]);
    await reader.read(ORG, 'run-1');
    expect(await reader.read('org-2', 'run-1')).to.deep.equal([]);
  });

  it('PERFB-15: a run whose session was erased since it was read is refused, not served from what was held', async () => {
    const w = new World();
    const reader = w.reader();
    await w.append('run-1', [outcome('i', { text: CANARY }), turnStarted(2)]);
    expect(await reader.read(ORG, 'run-1')).to.have.length(2);

    w.erased.add('run-1');
    for (const attempt of ['the first read after the erasure', 'a later read']) {
      expect(await rejection(reader.read(ORG, 'run-1')), attempt).to.be.instanceOf(SessionErasedError);
      expect(await rejection(reader.read(ORG, 'run-1', 1)), attempt).to.be.instanceOf(SessionErasedError);
    }
    reader.warm(ORG, 'run-1');
    await settle();
    expect(w.took(), 'nothing was taken again').to.equal(2);
  });

  it('PERFB-15: when the last event held is no longer in the store (an earlier journal was restored), the run is read again whole', async () => {
    const w = new World();
    const reader = w.reader({ pageSize: 2 });
    await w.append('run-1', [turnStarted(1), turnStarted(2), turnStarted(3)]);
    await reader.read(ORG, 'run-1');
    const restored = new World();
    await restored.append('run-1', [turnStarted(1), turnStarted(2)]);
    (w as { store: unknown }).store = restored.store;

    expect((await reader.read(ORG, 'run-1')).map((event) => event.seq)).to.deep.equal([1, 2]);
    expect(await reader.read(ORG, 'run-1')).to.deep.equal(await w.whole('run-1'));
  });

  it('a page that holds only the last event held does not end the read', async () => {
    const w = new World();
    const reader = w.reader({ pageSize: 1 });
    await w.append('run-1', [turnStarted(1), turnStarted(2)]);
    await reader.read(ORG, 'run-1');
    await w.append('run-1', [turnStarted(3), turnStarted(4)]);

    expect((await reader.read(ORG, 'run-1')).map((event) => event.seq)).to.deep.equal([1, 2, 3, 4]);
    expect(w.took()).to.equal(4);
  });
});
