/**
 * What every RunReadyNotifier must do, RR-1..RR-4 (QA-19): the in-memory notifier
 * (`run-ready-notifier.contract.test.ts`) and Redis pub/sub (`run-ready-notifier.redis.itest.ts`).
 * A listener plays the claimer that subscribes (`app/harness/run_ready.py`).
 */
import { expect } from 'chai';
import { RunReadyNotice, RunReadyNotifier } from '../../../../src/modules/agent-harness/ports/run-ready';

export interface RunReadyFixture {
  notifier: RunReadyNotifier;
  /** Resolves with the first `count` notices a claimer received, once it has them. */
  received(count: number): Promise<unknown[]>;
}

export interface RunReadyContractOptions {
  /** A notifier of the same kind whose backend cannot be reached (an adapter with no backend has none). */
  unreachable?: () => Promise<RunReadyNotifier>;
}

const notice = (runId: string, orgId = 'org-rr'): RunReadyNotice => ({ orgId, runId, kind: 'run_ready' });

export function describeRunReadyNotifierContract(
  name: string,
  create: () => Promise<RunReadyFixture>,
  options: RunReadyContractOptions = {},
): void {
  describe(`RunReadyNotifier contract: ${name}`, () => {
    it('RR-1: every notice of a batch reaches a claimer, in order', async () => {
      const { notifier, received } = await create();
      await notifier.notify([notice('run-1'), notice('run-2'), notice('run-3')]);
      expect(await received(3)).to.deep.equal([notice('run-1'), notice('run-2'), notice('run-3')]);
    });

    it('RR-2: a notice carries ids and the transition only', async () => {
      const { notifier, received } = await create();
      const withInput = { ...notice('run-4'), input: { query: 'private text' } } as RunReadyNotice;
      await notifier.notify([withInput]);
      expect(await received(1)).to.deep.equal([notice('run-4')]);
    });

    it('RR-3: an empty batch announces nothing', async () => {
      const { notifier, received } = await create();
      await notifier.notify([]);
      await notifier.notify([notice('run-5')]);
      expect(await received(1)).to.deep.equal([notice('run-5')]);
    });

    if (options.unreachable !== undefined) {
      const unreachable = options.unreachable;
      it('RR-4: an unreachable backend never rejects (the outbox row stays the truth)', async () => {
        const notifier = await unreachable();
        await notifier.notify([notice('run-6')]);
      });
    }
  });
}
