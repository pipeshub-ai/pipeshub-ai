import type { TFunction } from 'i18next';
import { formatRelativeTime } from '@/lib/utils/formatters';

/** GET /connectors/:id/progress — see ConnectorProgressResponse in the OpenAPI spec. */
export interface SyncRunCounts {
  found: number;
  new: number;
  changed: number;
  unchanged: number;
  removed: number;
  filtered: number;
  containers: number;
}

export interface SyncRunError {
  code: SyncErrorCode;
  detail?: string | null;
  runId?: string;
  at?: number;
}

export type SyncErrorCode =
  | 'auth'
  | 'permission'
  | 'rate_limit'
  | 'network'
  | 'source_unavailable'
  | 'not_found'
  | 'config'
  | 'incomplete'
  | 'interrupted'
  | 'unknown';

export interface SyncRun {
  runId: string;
  trigger: 'manual' | 'schedule' | 'enable' | 'startup' | 'resume' | 'follow_up' | 'unknown';
  full: boolean;
  startedAt: number;
  finishedAt?: number;
  phase?: 'starting' | 'people' | 'containers' | 'content' | 'finishing';
  lastActivityAt?: number;
  stalled?: boolean;
  throttledUntil?: number | null;
  outcome?: 'succeeded' | 'failed' | 'stopped' | 'skipped';
  counts: SyncRunCounts;
  filteredByReason?: Record<string, number>;
  unit?: { kind: string; done: number; total?: number | null } | null;
  expectedTotal?: number | null;
  approximate?: boolean;
  error?: SyncRunError;
  skipCode?: string;
  startedBy?: string;
}

export interface IndexingProgress {
  waiting: number;
  inProgress: number;
  failed: number;
  indexedRecently: number;
  perMinute: number;
  etaSeconds: number | null;
  waitingInLine: boolean;
}

export interface ReasonGroup {
  indexingStatus: string;
  reason: string | null;
  count: number;
}

export interface ConnectorProgress {
  connectorId: string;
  status: string;
  isActive: boolean;
  run: SyncRun | null;
  lastRun: SyncRun | null;
  recentRuns?: SyncRun[];
  lastError: SyncRunError | null;
  awaitingFirstSync: boolean;
  readyAt?: number | null;
  indexing: IndexingProgress;
  stats?: {
    stats: { total: number; indexingStatus: Record<string, number> };
    byReason?: ReasonGroup[];
  } | null;
}

export type ProgressTone = 'ok' | 'busy' | 'warn' | 'error' | 'off';

export interface ProgressLine {
  tone: ProgressTone;
  text: string;
  /** 0–100 only when the total is really known; otherwise no bar is drawn. */
  percent: number | null;
  /** Show an indeterminate bar: work is moving but its size is unknown. */
  moving: boolean;
}

const KNOWN_UNITS = new Set([
  'user', 'mailbox', 'drive', 'site', 'bucket', 'share', 'channel', 'space',
  'project', 'repository', 'table', 'feed', 'page', 'container', 'folder',
]);

const K = 'workspace.connectors.progress';

export function unitLabel(t: TFunction, kind: string): string {
  return t(`${K}.units.${KNOWN_UNITS.has(kind) ? kind : 'item'}`);
}

/** "about 10 min": coarse on purpose, since indexing speed varies. */
export function etaLabel(t: TFunction, seconds: number): string {
  const minutes = seconds / 60;
  if (minutes < 1) return t(`${K}.eta.underMinute`);
  if (minutes < 60) {
    const rounded = minutes < 10 ? Math.ceil(minutes) : Math.ceil(minutes / 5) * 5;
    return t(`${K}.eta.minutes`, { n: rounded });
  }
  if (minutes < 6 * 60) return t(`${K}.eta.hours`, { n: Math.round(minutes / 60) });
  return t(`${K}.eta.longer`);
}

export function errorTitle(t: TFunction, code: SyncErrorCode | undefined, source: string): string {
  return t(`${K}.error.${code ?? 'unknown'}.title`, { source });
}

export function errorAction(t: TFunction, code: SyncErrorCode | undefined, source: string): string {
  return t(`${K}.error.${code ?? 'unknown'}.action`, { source });
}

function clampPercent(done: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(99, Math.floor((done / total) * 100)));
}

function minutesSince(timestamp: number, now: number): number {
  return Math.max(1, Math.round((now - timestamp) / 60000));
}

/** What the run is doing, in one line. */
function describeRun(t: TFunction, run: SyncRun, source: string, now: number): ProgressLine {
  if (run.throttledUntil) {
    const time = new Date(run.throttledUntil).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    return { tone: 'warn', text: t(`${K}.run.throttled`, { source, time }), percent: null, moving: false };
  }
  if (run.stalled && run.lastActivityAt) {
    return {
      tone: 'warn',
      text: t(`${K}.run.stalled`, { n: minutesSince(run.lastActivityAt, now) }),
      percent: null,
      moving: false,
    };
  }
  const { counts } = run;
  if (run.unit && run.unit.total) {
    return {
      tone: 'busy',
      text: t(`${K}.run.units`, {
        source,
        unit: unitLabel(t, run.unit.kind),
        done: run.unit.done,
        total: run.unit.total,
        found: counts.found,
      }),
      percent: clampPercent(run.unit.done, run.unit.total),
      moving: true,
    };
  }
  if (run.expectedTotal) {
    return {
      tone: 'busy',
      text: t(`${K}.run.ofTotal`, { source, found: counts.found, total: run.expectedTotal }),
      percent: clampPercent(counts.found, run.expectedTotal),
      moving: true,
    };
  }
  const key =
    run.phase === 'people' ? 'people'
      : run.phase === 'containers' ? 'containers'
        : run.phase === 'finishing' ? 'finishing'
          : counts.found > 0 ? 'content'
            : 'starting';
  return {
    tone: 'busy',
    text: t(`${K}.run.${key}`, {
      source,
      found: counts.found,
      changed: counts.new + counts.changed,
      containers: counts.containers,
    }),
    percent: null,
    moving: true,
  };
}

/**
 * The one line a connector card shows: always answers "is it OK, and what is it doing?".
 * Order matters: a live sync beats indexing, which beats the last outcome.
 */
export function describeConnectorProgress(
  t: TFunction,
  view: ConnectorProgress,
  source: string,
  now: number = Date.now(),
): ProgressLine {
  if (view.status === 'DELETING') return { tone: 'off', text: t(`${K}.removing`), percent: null, moving: true };
  if (!view.isActive) return { tone: 'off', text: t(`${K}.off`), percent: null, moving: false };
  if (view.status === 'QUEUED') return { tone: 'busy', text: t(`${K}.queued`), percent: null, moving: false };
  if (view.run) return describeRun(t, view.run, source, now);

  const { indexing, lastRun } = view;
  const backlog = indexing.waiting + indexing.inProgress;
  // A stop proves nothing about the source, so a failure from before it still stands.
  if (lastRun?.outcome === 'stopped' && view.lastError != null) {
    return {
      tone: 'error',
      text: t(`${K}.stoppedAfterFailure`, { reason: errorTitle(t, view.lastError.code, source) }),
      percent: null,
      moving: false,
    };
  }
  if (lastRun?.outcome === 'failed') {
    return {
      tone: 'error',
      text: t(`${K}.lastFailed`, { reason: errorTitle(t, (lastRun.error ?? view.lastError)?.code, source) }),
      percent: null,
      moving: false,
    };
  }
  if (backlog > 0) {
    if (indexing.waitingInLine) {
      return { tone: 'busy', text: t(`${K}.indexing.inLine`, { n: backlog }), percent: null, moving: false };
    }
    return {
      tone: 'busy',
      text: indexing.etaSeconds != null
        ? t(`${K}.indexing.leftWithEta`, { n: backlog, eta: etaLabel(t, indexing.etaSeconds) })
        : t(`${K}.indexing.left`, { n: backlog }),
      percent: null,
      moving: true,
    };
  }
  if (lastRun?.outcome === 'skipped') {
    return { tone: 'warn', text: t(`${K}.skipped`), percent: null, moving: false };
  }
  if (lastRun?.outcome === 'stopped') {
    return { tone: 'warn', text: t(`${K}.stopped`), percent: null, moving: false };
  }
  if (!lastRun) {
    return { tone: 'off', text: t(`${K}.notSyncedYet`), percent: null, moving: false };
  }
  const base = lastRun.finishedAt
    ? t(`${K}.upToDateAgo`, { time: formatRelativeTime(lastRun.finishedAt) })
    : t(`${K}.upToDate`);
  return {
    tone: indexing.failed > 0 ? 'warn' : 'ok',
    text: indexing.failed > 0 ? t(`${K}.withFailures`, { base, n: indexing.failed }) : base,
    percent: null,
    moving: false,
  };
}

/** How often to ask again: often while something moves, rarely while indexing drains, not at all at rest. */
export function nextPollMs(view: ConnectorProgress | null | undefined): number | null {
  if (!view) return 5_000;
  if (view.run || view.status === 'QUEUED' || view.status === 'SYNCING' || view.status === 'FULL_SYNCING') return 5_000;
  if (view.indexing.waiting + view.indexing.inProgress > 0) return 30_000;
  return null;
}
