/**
 * What every RunWakeup must do, RW-1..RW-6: the in-memory wakeup
 * (`run-wakeup.contract.test.ts`) and Redis pub/sub (`run-wakeup.redis.itest.ts`).
 * A holder plays the worker that waits on the run's interaction (P2.6c).
 */
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import { RunWakeup } from '../../../../src/modules/agent-harness/ports/run-wakeup';

export interface WakeHolder {
  /** Resolves once the holder was woken at least `count` times. */
  woken(count: number): Promise<void>;
  readonly count: number;
  close(): Promise<void>;
}

export interface RunWakeupFixture {
  wakeup: RunWakeup;
  /** Resolves once the holder is listening, as a worker that subscribed. */
  hold(runId: string): Promise<WakeHolder>;
}

export interface RunWakeupContractOptions {
  /** A wakeup of the same kind whose backend cannot be reached (an adapter with no backend has none). */
  unreachable?: () => Promise<RunWakeup>;
}

/** Counts wakes and lets a test wait for the n-th; fixtures feed `wake()` from their transport. */
export function wakeCounter(close: () => Promise<void> = async () => undefined): WakeHolder & { wake(): void } {
  let count = 0;
  const waiters: Array<{ count: number; resolve: () => void }> = [];
  return {
    get count() {
      return count;
    },
    wake() {
      count += 1;
      for (const waiter of [...waiters]) {
        if (count >= waiter.count) {
          waiters.splice(waiters.indexOf(waiter), 1);
          waiter.resolve();
        }
      }
    },
    woken: (n) => (count >= n ? Promise.resolve() : new Promise<void>((resolve) => waiters.push({ count: n, resolve }))),
    close,
  };
}

export function describeRunWakeupContract(
  name: string,
  create: () => Promise<RunWakeupFixture>,
  options: RunWakeupContractOptions = {},
): void {
  describe(`RunWakeup contract: ${name}`, function () {
    this.timeout(20_000);
    let f: RunWakeupFixture;
    const holders: WakeHolder[] = [];
    const hold = async (runId: string): Promise<WakeHolder> => {
      const holder = await f.hold(runId);
      holders.push(holder);
      return holder;
    };
    const run = (): string => `run-${randomUUID()}`;

    beforeEach(async () => {
      f = await create();
    });

    afterEach(async () => {
      for (const holder of holders.splice(0)) await holder.close();
    });

    it("RW-1: a wake reaches the holder of the run's call", async () => {
      const runId = run();
      const holder = await hold(runId);
      await f.wakeup.wake(runId);
      await holder.woken(1);
      expect(holder.count).to.equal(1);
    });

    it("RW-2: another run's wake does not reach the holder", async () => {
      const mine = run();
      const holder = await hold(mine);
      const other = await hold(run());
      await f.wakeup.wake(run());
      await f.wakeup.wake(mine);
      await holder.woken(1);
      expect(holder.count).to.equal(1);
      expect(other.count).to.equal(0);
    });

    it('RW-3: every wake is delivered, none merged away', async () => {
      const runId = run();
      const holder = await hold(runId);
      for (let i = 0; i < 3; i += 1) await f.wakeup.wake(runId);
      await holder.woken(3);
      expect(holder.count).to.equal(3);
    });

    it('RW-4: waking a run nobody holds succeeds', async () => {
      await f.wakeup.wake(run());
    });

    it('RW-5: concurrent wakes of many runs reach each holder exactly once', async () => {
      const runs = Array.from({ length: 12 }, run);
      const held = await Promise.all(runs.map(hold));
      await Promise.all(runs.map((runId) => f.wakeup.wake(runId)));
      await Promise.all(held.map((holder) => holder.woken(1)));
      // Holders share one listener, so a later wake arriving proves no earlier duplicate is still in flight.
      await f.wakeup.wake(runs[0]!);
      await held[0]!.woken(2);
      expect(held.map((holder) => holder.count)).to.deep.equal([2, ...runs.slice(1).map(() => 1)]);
    });

    const { unreachable } = options;
    if (unreachable !== undefined) {
      it('RW-6: an unreachable backend fails the wake, for the caller to log (the resolution stands)', async () => {
        const broken = await unreachable();
        let failed = false;
        try {
          await broken.wake(run());
        } catch {
          failed = true;
        }
        expect(failed).to.equal(true);
      });
    }
  });
}
