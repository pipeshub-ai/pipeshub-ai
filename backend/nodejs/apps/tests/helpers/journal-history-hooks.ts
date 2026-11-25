/**
 * JRN-22: after every test, each in-memory journal and each in-memory
 * execution store's v1 events the test wrote must be a valid history
 * (`tests/support/harness/journal-history.ts`), for the runs it admitted.
 * Loaded by `.mocharc.yaml`;
 * a test that writes an invalid history on purpose calls
 * `uncheckedJournalHistory(reason)`.
 */
import { InMemoryExecutionStore } from '../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryJournalStore } from '../../src/modules/agent-harness/adapters/in-memory-journal-store';
import type { PreparedJournalAppend } from '../../src/modules/agent-harness/domain/journal';
import {
  describeViolations,
  journalHistoryViolations,
  takeUncheckedReason,
  v1HistoryViolations,
} from '../support/harness/journal-history';

const journals = new Map<InMemoryJournalStore, Map<string, [string, string]>>();
const sessions = new Map<InMemoryExecutionStore, Map<string, [string, string]>>();

const appendNow = InMemoryJournalStore.prototype.appendNow;
InMemoryJournalStore.prototype.appendNow = function tracked(
  this: InMemoryJournalStore,
  batch: PreparedJournalAppend,
) {
  const streams = journals.get(this) ?? new Map<string, [string, string]>();
  streams.set(JSON.stringify([batch.orgId, batch.streamId]), [batch.orgId, batch.streamId]);
  journals.set(this, streams);
  return appendNow.call(this, batch);
};

const admit = InMemoryExecutionStore.prototype.admit;
InMemoryExecutionStore.prototype.admit = async function tracked(
  this: InMemoryExecutionStore,
  ...args: Parameters<InMemoryExecutionStore['admit']>
) {
  const result = await admit.apply(this, args);
  const known = sessions.get(this) ?? new Map<string, [string, string]>();
  known.set(JSON.stringify([result.run.orgId, result.sessionId]), [result.run.orgId, result.sessionId]);
  sessions.set(this, known);
  return result;
};

const ALL = Number.MAX_SAFE_INTEGER;

async function violations(): Promise<string> {
  const found: string[] = [];
  for (const [journal, streams] of journals) {
    const events = (
      await Promise.all(
        [...streams.values()].map(([orgId, streamId]) => journal.readStream(orgId, streamId, 0, ALL)),
      )
    ).flat();
    const broken = journalHistoryViolations(events, { admittedOnly: true });
    if (broken.length > 0) found.push(describeViolations(broken));
  }
  for (const [store, known] of sessions) {
    for (const [orgId, sessionId] of known.values()) {
      const broken = v1HistoryViolations(await store.readEvents(orgId, sessionId, 0, ALL));
      if (broken.length > 0) found.push(describeViolations(broken));
    }
  }
  return found.join('\n');
}

export const mochaHooks = {
  async afterEach(this: Mocha.Context): Promise<void> {
    const unchecked = takeUncheckedReason();
    try {
      const failed = this.currentTest?.state === 'failed';
      if (unchecked !== null || failed) return;
      const found = await violations();
      if (found !== '') {
        // Fails the test that wrote it; throwing would fail the hook and skip every later test of the run.
        (this.test as Mocha.Hook).error(new Error(`invalid journal history (JRN-22):\n${found}`));
      }
    } finally {
      journals.clear();
      sessions.clear();
    }
  },
};
