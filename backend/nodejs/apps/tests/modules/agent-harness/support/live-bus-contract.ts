/**
 * What every LiveBus (and its LiveBusEraser side) must do, LB-1..LB-11 (gap
 * PRJ-G3): the in-memory bus (`live-bus.contract.test.ts`) and Redis Streams
 * (`live-bus.redis.itest.ts`). The fixture publishes the way the worker does,
 * so the reader is held to what a real publisher writes.
 */
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import { LiveDelta } from '../../../../src/modules/agent-harness/projection/frames';
import { LiveBus } from '../../../../src/modules/agent-harness/ports/live-bus';
import { LiveBusEraser } from '../../../../src/modules/agent-harness/ports/session-erasure';

export interface LiveBusFixture {
  bus: LiveBus & LiveBusEraser;
  publish(delta: LiveDelta): Promise<void>;
  /** The run's buffer lapsed (TTL or trimming), as the backend lets it. */
  expire(runId: string): Promise<void>;
  /** A bus of the same kind whose backend cannot be reached. */
  unreachable(): Promise<LiveBus>;
  close?(): Promise<void>;
}

export const liveDelta = (
  runId: string,
  n: number,
  overrides: Partial<LiveDelta> = {},
): LiveDelta => ({
  runId,
  fence: 1,
  modelAttempt: 1,
  n,
  kind: 'text_delta',
  data: { text: `token ${String(n)} ` },
  ...overrides,
});

interface Follower {
  readonly deltas: LiveDelta[];
  /** Resolves once at least `count` deltas arrived. */
  received(count: number): Promise<void>;
  close(): void;
}

function followInto(bus: LiveBus, runId: string, cursor: string): Follower {
  const deltas: LiveDelta[] = [];
  const waiters: Array<{ count: number; resolve: () => void }> = [];
  const subscription = bus.follow(
    runId,
    cursor,
    (batch) => {
      deltas.push(...batch);
      for (const waiter of [...waiters]) {
        if (deltas.length >= waiter.count) {
          waiters.splice(waiters.indexOf(waiter), 1);
          waiter.resolve();
        }
      }
    },
    () => undefined,
  );
  return {
    deltas,
    received: (count) =>
      deltas.length >= count
        ? Promise.resolve()
        : new Promise<void>((resolve) => waiters.push({ count, resolve })),
    close: () => subscription.close(),
  };
}

const ns = (deltas: readonly LiveDelta[]): number[] => deltas.map((d) => d.n);

export function describeLiveBusContract(
  name: string,
  create: () => Promise<LiveBusFixture>,
): void {
  describe(`LiveBus contract: ${name}`, function () {
    this.timeout(20_000);
    let f: LiveBusFixture;
    const open: Array<() => void> = [];
    const follow = (runId: string, cursor: string): Follower => {
      const follower = followInto(f.bus, runId, cursor);
      open.push(() => follower.close());
      return follower;
    };
    const run = (): string => `run-${randomUUID()}`;

    beforeEach(async () => {
      f = await create();
    });

    afterEach(async () => {
      for (const close of open.splice(0)) close();
      await f.close?.();
    });

    it('LB-1: a run nothing was published to reads empty, with a cursor to follow from', async () => {
      const retained = await f.bus.read(run());
      expect(retained.deltas).to.deep.equal([]);
      expect(retained.cursor).to.be.a('string');
    });

    it('LB-2: read returns every published delta of the run in publish order, fields intact', async () => {
      const runId = run();
      const published = [
        liveDelta(runId, 1),
        liveDelta(runId, 2, { kind: 'reasoning_delta', data: { text: 'thinking' } }),
        liveDelta(runId, 3, { kind: 'tool_call', fence: 2, modelAttempt: 3, data: { name: 'search', args: { q: 'x' } } }),
      ];
      for (const delta of published) await f.publish(delta);
      expect((await f.bus.read(runId)).deltas).to.deep.equal(published);
    });

    it("LB-3: following from read's cursor delivers exactly what came after, including a delta published in between", async () => {
      const runId = run();
      await f.publish(liveDelta(runId, 1));
      const retained = await f.bus.read(runId);
      await f.publish(liveDelta(runId, 2));
      const follower = follow(runId, retained.cursor);
      await f.publish(liveDelta(runId, 3));
      await follower.received(2);
      await f.publish(liveDelta(runId, 4));
      await follower.received(3);
      expect(ns(follower.deltas)).to.deep.equal([2, 3, 4]);
    });

    it('LB-4: a follower hears only its own run', async () => {
      const mine = run();
      const other = run();
      const follower = follow(mine, (await f.bus.read(mine)).cursor);
      await f.publish(liveDelta(other, 1));
      await f.publish(liveDelta(mine, 7));
      await follower.received(1);
      expect(follower.deltas).to.deep.equal([liveDelta(mine, 7)]);
      expect((await f.bus.read(other)).deltas).to.deep.equal([liveDelta(other, 1)]);
    });

    it('LB-5: a closed follower hears nothing more, and closing twice is safe', async () => {
      const runId = run();
      const { cursor } = await f.bus.read(runId);
      const closed = follow(runId, cursor);
      const witness = follow(runId, cursor);
      await f.publish(liveDelta(runId, 1));
      await closed.received(1);
      closed.close();
      closed.close();
      await f.publish(liveDelta(runId, 2));
      await witness.received(2);
      expect(ns(closed.deltas)).to.deep.equal([1]);
    });

    it('LB-6: every follower of a run hears every delta', async () => {
      const runId = run();
      const { cursor } = await f.bus.read(runId);
      const a = follow(runId, cursor);
      const b = follow(runId, cursor);
      for (const n of [1, 2, 3]) await f.publish(liveDelta(runId, n));
      await Promise.all([a.received(3), b.received(3)]);
      expect(ns(a.deltas)).to.deep.equal([1, 2, 3]);
      expect(ns(b.deltas)).to.deep.equal([1, 2, 3]);
    });

    it('LB-7: erase drops the named runs, counts only those that had a buffer, and keeps the others', async () => {
      const [a, b, kept, never] = [run(), run(), run(), run()];
      for (const runId of [a, b, kept]) await f.publish(liveDelta(runId, 1));
      expect(await f.bus.erase([a, b, never])).to.equal(2);
      expect((await f.bus.read(a)).deltas).to.deep.equal([]);
      expect((await f.bus.read(b)).deltas).to.deep.equal([]);
      expect((await f.bus.read(kept)).deltas).to.deep.equal([liveDelta(kept, 1)]);
      expect(await f.bus.erase([a])).to.equal(0);
      expect(await f.bus.erase([])).to.equal(0);
    });

    it('LB-8: a lapsed buffer reads empty; deltas published after it are followed again', async () => {
      const runId = run();
      await f.publish(liveDelta(runId, 1));
      const { cursor } = await f.bus.read(runId);
      await f.expire(runId);
      expect((await f.bus.read(runId)).deltas).to.deep.equal([]);
      const follower = follow(runId, cursor);
      await f.publish(liveDelta(runId, 2));
      await follower.received(1);
      expect(ns(follower.deltas)).to.deep.equal([2]);
    });

    it('LB-9: an unreachable backend fails read, and reports to a follower only after follow returned', async () => {
      const broken = await f.unreachable();
      let readFailed = false;
      try {
        await broken.read(run());
      } catch {
        readFailed = true;
      }
      expect(readFailed).to.equal(true);
      let returned = false;
      let reportedBeforeReturn = false;
      const reported = new Promise<unknown>((resolve) => {
        const subscription = broken.follow(
          run(),
          '0',
          () => expect.fail('an unreachable bus delivered a delta'),
          (error) => {
            reportedBeforeReturn = !returned;
            resolve(error);
          },
        );
        returned = true;
        open.push(() => subscription.close());
      });
      expect(await reported).to.be.instanceOf(Error);
      expect(reportedBeforeReturn).to.equal(false);
    });

    it('LB-10: concurrent publishers on many runs: each follower gets its run, in order, once', async () => {
      const runs = Array.from({ length: 6 }, run);
      const watched = await Promise.all(
        runs.map(async (runId) => follow(runId, (await f.bus.read(runId)).cursor)),
      );
      await Promise.all(
        runs.map(async (runId) => {
          for (let n = 1; n <= 15; n += 1) await f.publish(liveDelta(runId, n));
        }),
      );
      await Promise.all(watched.map((follower) => follower.received(15)));
      const expected = Array.from({ length: 15 }, (_, i) => i + 1);
      for (const [index, follower] of watched.entries()) {
        expect(ns(follower.deltas), runs[index]).to.deep.equal(expected);
        expect(follower.deltas.every((d) => d.runId === runs[index])).to.equal(true);
      }
    });

    it('LB-11: a burst larger than one read batch arrives whole, in order, without duplicates', async () => {
      const runId = run();
      const follower = follow(runId, (await f.bus.read(runId)).cursor);
      const burst = 600;
      for (let n = 1; n <= burst; n += 1) await f.publish(liveDelta(runId, n));
      await follower.received(burst);
      expect(ns(follower.deltas)).to.deep.equal(Array.from({ length: burst }, (_, i) => i + 1));
      expect((await f.bus.read(runId)).deltas).to.have.length(burst);
    });
  });
}
