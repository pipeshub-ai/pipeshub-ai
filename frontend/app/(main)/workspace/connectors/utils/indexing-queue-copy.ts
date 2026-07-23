import type { IndexingQueueStatus } from '../types';

/** Hide the queue line for trivial backlog so healthy syncs stay clean. */
export const INDEXING_QUEUE_VISIBLE_LAG = 10;

export interface IndexingQueueCopy {
  /** i18n key under workspace.connectors.syncProgress.queue.* */
  key: string;
  /** English fallback for `t(key, { defaultValue, ...params })`. */
  text: string;
  params: Record<string, number | string>;
}

/**
 * Undelivered stream lag plus consumer PEL. Lag alone goes to 0 once messages
 * are delivered, even when the indexer is still chewing through (or retrying)
 * them — which is exactly when "Indexing 0 of N" feels stuck.
 */
export function indexingQueueBacklog(
  queue: IndexingQueueStatus | null | undefined
): number {
  if (!queue) return 0;
  return Math.max(0, Math.round(queue.lag)) + Math.max(0, Math.round(queue.pending));
}

/**
 * Format a rough ETA band. Prefer ranges over false precision — drain rate
 * swings hard when embeddings time out on CPU.
 */
export function formatQueueEta(etaSeconds: number | null | undefined): IndexingQueueCopy | null {
  if (etaSeconds == null || etaSeconds < 0) return null;
  if (etaSeconds === 0) {
    return {
      key: 'workspace.connectors.syncProgress.queue.etaAlmostDone',
      text: 'almost done',
      params: {},
    };
  }
  if (etaSeconds < 90) {
    return {
      key: 'workspace.connectors.syncProgress.queue.etaAboutAMinute',
      text: 'about a minute',
      params: {},
    };
  }
  const minutes = Math.ceil(etaSeconds / 60);
  if (minutes <= 5) {
    return {
      key: 'workspace.connectors.syncProgress.queue.etaMinutes',
      text: '~{{minutes}} min',
      params: { minutes },
    };
  }
  if (minutes <= 15) {
    return {
      key: 'workspace.connectors.syncProgress.queue.etaMinutesRange',
      text: '~{{low}}–{{high}} min',
      params: { low: 5, high: 15 },
    };
  }
  if (minutes <= 45) {
    return {
      key: 'workspace.connectors.syncProgress.queue.etaMinutesRange',
      text: '~{{low}}–{{high}} min',
      params: { low: 15, high: 45 },
    };
  }
  if (minutes <= 90) {
    return {
      key: 'workspace.connectors.syncProgress.queue.etaAboutAnHour',
      text: 'about an hour',
      params: {},
    };
  }
  return {
    key: 'workspace.connectors.syncProgress.queue.etaMayTakeAWhile',
    text: 'may take a while',
    params: {},
  };
}

/**
 * Whether to surface the shared-queue line under run progress.
 * Shown during indexing when the org stream still has meaningful backlog.
 */
export function shouldShowIndexingQueue(
  queue: IndexingQueueStatus | null | undefined,
  opts: { indexing: boolean }
): boolean {
  if (!opts.indexing) return false;
  return indexingQueueBacklog(queue) >= INDEXING_QUEUE_VISIBLE_LAG;
}

/** Compact card hint — no ETA. */
export function describeIndexingQueueCompact(): IndexingQueueCopy {
  return {
    key: 'workspace.connectors.syncProgress.queue.waitingCompact',
    text: 'waiting in queue',
    params: {},
  };
}

/** Detail/overview line with jobs-ahead and optional ETA. */
export function describeIndexingQueueDetail(
  queue: IndexingQueueStatus
): { jobs: IndexingQueueCopy; eta: IndexingQueueCopy | null } {
  const backlog = indexingQueueBacklog(queue);
  return {
    jobs: {
      key: 'workspace.connectors.syncProgress.queue.waitingDetail',
      text: 'Waiting in shared indexing queue · ~{{count}} jobs ahead',
      params: { count: backlog },
    },
    eta: formatQueueEta(queue.etaSeconds),
  };
}
