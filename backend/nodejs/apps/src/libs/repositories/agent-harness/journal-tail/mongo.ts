import { bindOrgContext } from '../../../context/orgContext';
import {
  HarnessLogger,
  harnessLogMeta,
  silentHarnessLogger,
} from '../../../../modules/agent-harness/application/harness-log';
import {
  JournalTail,
  JournalTailSubscription,
} from '../../../../modules/agent-harness/ports/journal-tail';
import { SessionEventModel } from '../journal/schema/journal.schema';

/**
 * Cross-replica journal tail (REPORT 6.2.4): one change stream per process on
 * `sessionEvents` inserts, fanned out to the streams watching each run. A
 * slow safety poll covers a missed notification; without change streams
 * (standalone Mongo, a lost cursor) the poll alone drives the streams.
 */

const SAFETY_POLL_MS = 5_000;
const FALLBACK_POLL_MS = 1_000;
const REOPEN_MS = 5_000;

interface ChangeStreamLike {
  on(event: 'change', listener: (change: unknown) => void): unknown;
  on(event: 'error' | 'close', listener: (error?: unknown) => void): unknown;
  close(): Promise<unknown>;
}

export type OpenChangeStream = () => ChangeStreamLike;

const openSessionEventInserts: OpenChangeStream = () =>
  SessionEventModel.collection.watch(
    [
      { $match: { operationType: 'insert' } },
      {
        $project: {
          'fullDocument.orgId': 1,
          'fullDocument.runId': 1,
          'fullDocument.seq': 1,
        },
      },
    ],
    {},
  );

export class MongoJournalTail implements JournalTail {
  private readonly watchers = new Map<string, Set<(seq?: number) => void>>();
  private stream: ChangeStreamLike | null = null;
  private live = false;
  private timer: NodeJS.Timeout | null = null;
  private reopen: NodeJS.Timeout | null = null;
  private degradedLogged = false;

  constructor(
    private readonly open: OpenChangeStream = openSessionEventInserts,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

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
    this.start();
    return {
      close: () => {
        set.delete(told);
        if (set.size === 0) this.watchers.delete(key);
        if (this.watchers.size === 0) this.stop();
      },
    };
  }

  private start(): void {
    if (this.stream === null && this.reopen === null) this.openStream();
    this.schedulePoll();
  }

  private openStream(): void {
    try {
      const stream = this.open();
      this.stream = stream;
      this.live = true;
      this.degradedLogged = false;
      stream.on('change', (change) => {
        this.notify(change);
      });
      stream.on('error', (error) => {
        if (this.stream === stream) this.degrade(error);
      });
      stream.on('close', () => {
        if (this.stream === stream) this.degrade(undefined);
      });
    } catch (error) {
      this.degrade(error);
    }
  }

  private degrade(error: unknown): void {
    const wasLive = this.live;
    this.live = false;
    const stream = this.stream;
    this.stream = null;
    if (stream !== null) void stream.close().catch(() => undefined);
    if (wasLive || !this.degradedLogged) {
      this.degradedLogged = true;
      this.logger.warn(
        'projection.tail_degraded',
        harnessLogMeta('projection.tail_degraded', {
          reason: 'change_stream_unavailable',
          errorType: error instanceof Error ? error.name : 'closed',
          pollMs: FALLBACK_POLL_MS,
        }),
      );
    }
    if (this.watchers.size > 0 && this.reopen === null) {
      this.reopen = setTimeout(() => {
        this.reopen = null;
        if (this.watchers.size > 0) this.openStream();
      }, REOPEN_MS);
      this.reopen.unref();
    }
  }

  private notify(change: unknown): void {
    const doc = (
      change as {
        fullDocument?: { orgId?: string; runId?: string; seq?: unknown };
      }
    ).fullDocument;
    if (doc?.orgId === undefined || doc.runId === undefined) return;
    const seq = typeof doc.seq === 'number' ? doc.seq : undefined;
    for (const watcher of this.watchers.get(`${doc.orgId}:${doc.runId}`) ??
      []) {
      watcher(seq);
    }
  }

  private schedulePoll(): void {
    if (this.timer !== null) return;
    this.timer = setTimeout(
      () => {
        this.timer = null;
        if (this.watchers.size === 0) return;
        for (const set of this.watchers.values())
          for (const watcher of set) watcher();
        this.schedulePoll();
      },
      this.live ? SAFETY_POLL_MS : FALLBACK_POLL_MS,
    );
    this.timer.unref();
  }

  private stop(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    if (this.reopen !== null) clearTimeout(this.reopen);
    this.timer = null;
    this.reopen = null;
    const stream = this.stream;
    this.stream = null;
    this.live = false;
    if (stream !== null) void stream.close().catch(() => undefined);
  }
}
