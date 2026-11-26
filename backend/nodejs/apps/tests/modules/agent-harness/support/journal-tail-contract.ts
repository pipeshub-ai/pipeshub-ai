/**
 * What every `JournalTail` must do (REPORT 6.2.4, PRJ-G3): signal the
 * watchers of a run whose journal grew, on whichever replica committed it.
 * `InMemoryJournalTail` (`journal-tail.test.ts`) and `MongoJournalTail` over a
 * change stream (`journal-tail.mongo.itest.ts`). No case waits on a timer:
 * each awaits a signal, bounded only by mocha's timeout. A signal for a run
 * that did not grow is allowed by the port (a safety poll), so the negative
 * cases are read at a barrier signal that the change stream orders after them.
 */
import { expect } from 'chai';
import {
  JournalTail,
  JournalTailSubscription,
} from '../../../../src/modules/agent-harness/ports/journal-tail';

export interface JournalTailBackend {
  tail: JournalTail;
  /** Commits a journal event for `runId` the way a replica does; the seq it committed. */
  grow(orgId: string, runId: string): Promise<number>;
}

// Org ids in the shape every store takes (24 hex): a PostgreSQL org row refuses any other.
const ORG_1 = '00000000000000000000a001';
const ORG_2 = '00000000000000000000a002';
const ORG_PROBE = '00000000000000000000a0ff';

interface Watcher {
  count: number;
  /** The seq each signal named; `undefined` for a signal that named none. */
  seqs: Array<number | undefined>;
  signalled: Promise<void>;
  subscription: JournalTailSubscription;
}

export function describeJournalTailContract(
  name: string,
  create: () => Promise<JournalTailBackend>,
): void {
  describe(`JournalTail contract: ${name}`, () => {
    let backend: JournalTailBackend;
    let open: JournalTailSubscription[];

    const watch = (orgId: string, runId: string): Watcher => {
      let resolve: () => void = () => undefined;
      const watcher = {
        count: 0,
        seqs: [] as Array<number | undefined>,
        signalled: new Promise<void>((done) => {
          resolve = done;
        }),
      } as Watcher;
      watcher.subscription = backend.tail.watch(orgId, runId, (seq) => {
        watcher.count += 1;
        watcher.seqs.push(seq);
        resolve();
      });
      open.push(watcher.subscription);
      return watcher;
    };

    beforeEach(async () => {
      open = [];
      backend = await create();
      // Holds the tail open and proves it live, so later commits are not missed.
      const probe = watch(ORG_PROBE, 'run-probe');
      await backend.grow(ORG_PROBE, 'run-probe');
      await probe.signalled;
    });

    afterEach(() => {
      for (const subscription of open) subscription.close();
    });

    it('JT-1: a commit to the watched run signals its watcher', async () => {
      const watcher = watch(ORG_1, 'run-a');
      await backend.grow(ORG_1, 'run-a');
      await watcher.signalled;
      expect(watcher.count).to.be.at.least(1);
    });

    it('JT-2: every watcher of the run is signalled', async () => {
      const watchers = [watch(ORG_1, 'run-a'), watch(ORG_1, 'run-a'), watch(ORG_1, 'run-a')];
      await backend.grow(ORG_1, 'run-a');
      await Promise.all(watchers.map((watcher) => watcher.signalled));
    });

    it("JT-3: another run's commit, or the same run id in another org, does not signal the watcher", async () => {
      const watched = watch(ORG_1, 'run-a');
      const barrier = watch(ORG_1, 'run-barrier');
      await backend.grow(ORG_2, 'run-a');
      await backend.grow(ORG_1, 'run-b');
      await backend.grow(ORG_1, 'run-barrier');
      await barrier.signalled;
      expect(watched.count).to.equal(0);
      await backend.grow(ORG_1, 'run-a');
      await watched.signalled;
    });

    it('JT-4: a closed subscription is not signalled; the run\'s other watchers still are', async () => {
      const closed = watch(ORG_1, 'run-a');
      const kept = watch(ORG_1, 'run-a');
      closed.subscription.close();
      closed.subscription.close();
      await backend.grow(ORG_1, 'run-a');
      await kept.signalled;
      expect(closed.count).to.equal(0);
    });

    it('JT-5: concurrent commits to several runs each signal their own watchers', async () => {
      const runs = ['run-1', 'run-2', 'run-3', 'run-4', 'run-5'];
      const watchers = runs.map((runId) => watch(ORG_1, runId));
      await Promise.all(runs.map((runId) => backend.grow(ORG_1, runId)));
      await Promise.all(watchers.map((watcher) => watcher.signalled));
    });

    it('JT-6 (PERFD-12): a signal for a committed event names its seq; a signal never names a seq that was not committed', async () => {
      const first = watch(ORG_1, 'run-a');
      const committed = [await backend.grow(ORG_1, 'run-a')];
      await first.signalled;
      const second = watch(ORG_1, 'run-a');
      committed.push(await backend.grow(ORG_1, 'run-a'));
      await second.signalled;

      expect(second.seqs).to.include(committed[1]);
      for (const seq of [...first.seqs, ...second.seqs]) {
        if (seq !== undefined) expect(committed).to.include(seq);
      }
    });
  });
}
