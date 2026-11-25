import { bindOrgContext } from '../../../libs/context/orgContext';
import { SessionErasedError } from '../domain/errors';
import { StoreClock, systemStoreClock } from '../domain/execution';
import { JournalEvent } from '../domain/journal';
import { JournalStore } from '../ports/journal-store';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { JournalService } from './journal-service';

export const RUN_JOURNAL_PAGE = 500;

/** What one Node keeps of every run's journal together. */
export const RUN_JOURNAL_MAX_BYTES = 64 * 1024 * 1024;

/**
 * A run nobody read for this long is dropped: what is held is plaintext, as
 * a stream's projector holds it, and must not outlive its readers by much.
 */
export const RUN_JOURNAL_IDLE_MS = 120_000;

export interface RunJournalReaderOptions {
  journal: Pick<JournalStore<unknown>, 'readRun'>;
  journals: Pick<JournalService, 'resolve'>;
  clock?: StoreClock;
  logger?: HarnessLogger;
  maxBytes?: number;
  idleMs?: number;
  pageSize?: number;
}

interface HeldRun {
  orgId: string;
  runId: string;
  /** Every event of the run up to the last one here, resolved, in order. */
  events: JournalEvent[];
  bytes: number;
  touchedAt: number;
  reading: Promise<void> | null;
  /** The highest seq whoever started `reading` knew to be committed before it began. */
  covers: number;
  /** The read that starts when `reading` ends, shared by whoever asked meanwhile. */
  next: Promise<void> | null;
  nextCovers: number;
}

interface Follower {
  after: () => number;
  gained: (events: JournalEvent[]) => void;
}

export interface RunJournalFollowing {
  close(): void;
}

const settled = (): void => undefined;

/**
 * Freezes `value` in depth and returns roughly what it weighs in memory.
 * Held events are shared by every reader, so none may change them.
 */
function freezeAndWeigh(value: unknown): number {
  if (typeof value === 'string') return 2 * value.length;
  if (value === null || typeof value !== 'object') return 8;
  // Binary views cannot be frozen; resolved events are JSON and hold none.
  if (ArrayBuffer.isView(value)) return value.byteLength;
  let bytes = 16;
  if (Array.isArray(value)) {
    for (const item of value) bytes += freezeAndWeigh(item);
  } else {
    for (const [key, item] of Object.entries(value)) {
      bytes += 2 * key.length + freezeAndWeigh(item);
    }
  }
  Object.freeze(value);
  return bytes;
}

/** The index of the first event after `afterSeq`; events are in seq order. */
function firstAfter(events: readonly JournalEvent[], afterSeq: number): number {
  let low = 0;
  let high = events.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if ((events[middle] as JournalEvent).seq > afterSeq) high = middle;
    else low = middle + 1;
  }
  return low;
}

/**
 * A run's resolved journal, read from the store once per Node (QA22-F3,
 * QA22-F4). Every read asks the store for the events after the last one
 * held, so an answer is always the store's own; what is held is not read,
 * unsealed or resolved again. The journal is append-only and a stream's seqs
 * commit in order, so events after the last one held are exactly the ones
 * not held. Erasure tombstones events in place, which the store refuses to
 * read: every read includes the last event held, so an erased run is refused
 * and dropped, not served from memory.
 *
 * A caller that knows a seq was committed before it asked (the commit that
 * appended it, a tail notice that names it) says so. A store read that began
 * knowing as much answers that caller too: one commit is read once, by its
 * own receipt, its read-ahead and the streams following the run.
 */
export class RunJournalReader {
  private readonly journal: Pick<JournalStore<unknown>, 'readRun'>;
  private readonly journals: Pick<JournalService, 'resolve'>;
  private readonly clock: StoreClock;
  private readonly logger: HarnessLogger;
  private readonly maxBytes: number;
  private readonly idleMs: number;
  private readonly pageSize: number;
  /** Least recently read first. */
  private readonly held = new Map<string, HeldRun>();
  private bytes = 0;
  private readonly followers = new Map<string, Set<Follower>>();

  constructor(options: RunJournalReaderOptions) {
    this.journal = options.journal;
    this.journals = options.journals;
    this.clock = options.clock ?? systemStoreClock;
    this.logger = options.logger ?? silentHarnessLogger;
    this.maxBytes = options.maxBytes ?? RUN_JOURNAL_MAX_BYTES;
    this.idleMs = options.idleMs ?? RUN_JOURNAL_IDLE_MS;
    this.pageSize = options.pageSize ?? RUN_JOURNAL_PAGE;
  }

  /**
   * The run's events after `afterSeq`, as the store holds them when this
   * returns. `committed`: a seq of the run the caller knows was in the store
   * before it asked; without one, everything committed until now is wanted.
   */
  async read(
    orgId: string,
    runId: string,
    afterSeq = 0,
    committed?: number,
  ): Promise<JournalEvent[]> {
    this.dropIdle();
    const key = `${orgId}:${runId}`;
    let run = this.held.get(key);
    if (run === undefined) {
      run = {
        orgId,
        runId,
        events: [],
        bytes: 0,
        touchedAt: this.clock.now(),
        reading: null,
        covers: 0,
        next: null,
        nextCovers: 0,
      };
      this.held.set(key, run);
    }
    await this.advance(run, committed);
    const events = run.events.slice(firstAfter(run.events, afterSeq));
    this.touch(key, run);
    this.fit(key);
    return events;
  }

  /**
   * Hands `gained` the run's events after `after()` whenever a store read by
   * anyone on this Node ends holding some: a stream following a run is given
   * what the run's own commit read, as that read answers, and reads nothing
   * for it. Nothing is handed over without a store read, and a follower that
   * hears nothing (another Node committed, the run is not held) still has
   * the journal tail's signal.
   */
  follow(
    orgId: string,
    runId: string,
    after: () => number,
    gained: (events: JournalEvent[]) => void,
  ): RunJournalFollowing {
    // Told in the context it was registered in (the viewer's request), never in the poller's or publisher's.
    const told = bindOrgContext(gained);
    const key = `${orgId}:${runId}`;
    const follower: Follower = { after, gained: told };
    const set = this.followers.get(key) ?? new Set<Follower>();
    set.add(follower);
    this.followers.set(key, set);
    return {
      close: () => {
        set.delete(follower);
        if (set.size === 0 && this.followers.get(key) === set) {
          this.followers.delete(key);
        }
      },
    };
  }

  /** How many followers there are; a stream that ended leaves none. */
  get following(): number {
    let count = 0;
    for (const set of this.followers.values()) count += set.size;
    return count;
  }

  private tell(run: HeldRun): void {
    const followers = this.followers.get(`${run.orgId}:${run.runId}`);
    if (followers === undefined) return;
    for (const follower of [...followers]) {
      const events = run.events.slice(firstAfter(run.events, follower.after()));
      if (events.length > 0) follower.gained(events);
    }
  }

  /**
   * Reads what the run's journal gained, off the caller's path, so the read
   * that needs it (the terminal commit's) finds it held. A failure only
   * leaves that read more to do.
   */
  warm(orgId: string, runId: string, committed?: number): void {
    this.read(orgId, runId, 0, committed).catch((error: unknown) => {
      this.logger.debug(
        'run_journal.warm_failed',
        harnessLogMeta('run_journal.warm_failed', {
          orgId,
          runId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'read_on_demand',
        }),
      );
    });
  }

  /**
   * One store read at a time per run. A caller that arrives while one is in
   * flight waits for the next: the one in flight may have started before the
   * commit the caller is asking about, unless it began knowing of a seq at
   * least as late as the one the caller names.
   */
  private advance(run: HeldRun, committed?: number): Promise<void> {
    if (run.reading === null) {
      run.covers = committed ?? 0;
      run.reading = this.readTail(run)
        .then(() => {
          this.tell(run);
        })
        .finally(() => {
          run.reading = null;
          run.covers = 0;
        });
      return run.reading;
    }
    if (committed !== undefined && committed <= run.covers) return run.reading;
    run.nextCovers = Math.max(run.nextCovers, committed ?? 0);
    run.next ??= run.reading.then(settled, settled).then(() => {
      const covers = run.nextCovers;
      run.next = null;
      run.nextCovers = 0;
      return this.advance(run, covers);
    });
    return run.next;
  }

  private async readTail(run: HeldRun): Promise<void> {
    // The first read starts at the last event held, not after it. Erasure
    // tombstones every event of a session and the store refuses a tombstone,
    // so a run erased since it was read is refused here, never served.
    let anchor = run.events[run.events.length - 1];
    let after = run.events[run.events.length - 2]?.seq ?? 0;
    for (;;) {
      let page: JournalEvent[];
      try {
        page = await this.journal.readRun(
          run.orgId,
          run.runId,
          after,
          this.pageSize,
        );
      } catch (error) {
        if (error instanceof SessionErasedError) this.release(run);
        throw error;
      }
      const full = page.length === this.pageSize;
      if (anchor !== undefined) {
        if (page[0]?.seq !== anchor.seq) {
          // The last event held is not in the store (a journal restored in place): read the run again.
          this.release(run);
          anchor = undefined;
          after = 0;
          continue;
        }
        after = anchor.seq;
        page = page.slice(1);
        anchor = undefined;
      }
      const last = page[page.length - 1];
      if (last !== undefined) {
        const resolved = await this.journals.resolve(page);
        let bytes = 0;
        for (const event of resolved) bytes += freezeAndWeigh(event);
        run.events.push(...resolved);
        run.bytes += bytes;
        if (this.isHeld(run)) this.bytes += bytes;
        after = last.seq;
      }
      if (!full) return;
    }
  }

  private isHeld(run: HeldRun): boolean {
    return this.held.get(`${run.orgId}:${run.runId}`) === run;
  }

  /** Lets go of every event held of `run`; a read in flight on it starts over. */
  private release(run: HeldRun): void {
    if (this.isHeld(run)) this.bytes -= run.bytes;
    run.events.length = 0;
    run.bytes = 0;
  }

  private touch(key: string, run: HeldRun): void {
    run.touchedAt = this.clock.now();
    if (this.held.get(key) !== run) return;
    this.held.delete(key);
    this.held.set(key, run);
  }

  private drop(key: string): void {
    const run = this.held.get(key);
    if (run === undefined) return;
    this.held.delete(key);
    this.bytes -= run.bytes;
  }

  private dropIdle(): void {
    const before = this.clock.now() - this.idleMs;
    for (const [key, run] of this.held) {
      if (run.touchedAt > before) return;
      // One being read is in use, whatever its last answer's age.
      if (run.reading === null) this.drop(key);
    }
  }

  /** Over budget: the least recently read runs go first, and `current` last. */
  private fit(current: string): void {
    for (const key of this.held.keys()) {
      if (this.bytes <= this.maxBytes) return;
      if (key !== current) this.drop(key);
    }
    if (this.bytes > this.maxBytes) this.drop(current);
  }
}
