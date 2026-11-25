import { bindOrgContext } from '../../../libs/context/orgContext';
import { JournalTail, JournalTailSubscription } from '../ports/journal-tail';

/** A tail signalled by hand (tests) or by an in-process store. */
export class InMemoryJournalTail implements JournalTail {
  private readonly watchers = new Map<string, Set<(seq?: number) => void>>();

  watch(
    orgId: string,
    runId: string,
    onChange: (seq?: number) => void,
  ): JournalTailSubscription {
    // Told in the context it was registered in (the viewer's request), never in the poller's or publisher's.
    const told = bindOrgContext(onChange);
    const key = `${orgId}:${runId}`;
    const set = this.watchers.get(key) ?? new Set();
    set.add(told);
    this.watchers.set(key, set);
    return { close: () => set.delete(told) };
  }

  /** `seq`: the committed event the signal is about; without one, as a safety poll signals. */
  changed(orgId: string, runId: string, seq?: number): void {
    for (const watcher of this.watchers.get(`${orgId}:${runId}`) ?? [])
      watcher(seq);
  }

  get watching(): number {
    let count = 0;
    for (const set of this.watchers.values()) count += set.size;
    return count;
  }
}
