/** The JournalTail contract on `InMemoryJournalTail`, signalled as an in-process store does. */
import { InMemoryJournalTail } from '../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { describeJournalTailContract } from './support/journal-tail-contract';

describeJournalTailContract('in-memory', async () => {
  const tail = new InMemoryJournalTail();
  const seqs = new Map<string, number>();
  return {
    tail,
    grow: async (orgId, runId) => {
      const seq = (seqs.get(`${orgId}:${runId}`) ?? 0) + 1;
      seqs.set(`${orgId}:${runId}`, seq);
      tail.changed(orgId, runId, seq);
      return seq;
    },
  };
});
