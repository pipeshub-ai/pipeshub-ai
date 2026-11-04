import type {
  ConnectorStatsResponse,
  ExtractionStatusCounts,
  RecordsStatus,
} from '@/app/(main)/workspace/connectors/types';

/**
 * Progress of one enrichment stage (classification or entity extraction) over the
 * records that reached it. NOT_STARTED records are not yet searchable, so they are
 * left out; a paused stage (model provider down) is still waiting, not stuck.
 */
export interface StageProgress {
  completed: number;
  waiting: number;
  failed: number;
  skipped: number;
}

function stageProgress(counts: ExtractionStatusCounts): StageProgress {
  return {
    completed: counts.COMPLETED ?? 0,
    waiting: (counts.QUEUED ?? 0) + (counts.IN_PROGRESS ?? 0) + (counts.PAUSED ?? 0),
    failed: counts.FAILED ?? 0,
    skipped: counts.SKIPPED ?? 0,
  };
}

/** Records the stage still applies to: everything it reached except the skipped ones. */
export function stageApplicableCount(progress: StageProgress): number {
  return progress.completed + progress.waiting + progress.failed;
}

/** Null when the backend predates pipeline stages and sends no extractionStatus. */
export function deriveClassificationStatus(
  stats?: ConnectorStatsResponse['data'] | null
): StageProgress | null {
  const ext = stats?.stats?.extractionStatus;
  return ext ? stageProgress(ext) : null;
}

/** Null when the backend predates the entities stage and sends no entitiesStatus. */
export function deriveEntitiesStatus(
  stats?: ConnectorStatsResponse['data'] | null
): StageProgress | null {
  const ent = stats?.stats?.entitiesStatus;
  return ent ? stageProgress(ent) : null;
}

export function deriveRecordsStatus(
  stats?: ConnectorStatsResponse['data'] | null
): RecordsStatus {
  if (!stats?.stats) {
    return {
      total: 0,
      completed: 0,
      failed: 0,
      unsupported: 0,
      inProgress: 0,
      notStarted: 0,
      autoIndexOff: 0,
      queued: 0,
      empty: 0,
      passwordProtected: 0,
    };
  }

  const idx = stats.stats.indexingStatus;
  return {
    total: stats.stats.total,
    completed: idx.COMPLETED ?? 0,
    failed: idx.FAILED ?? 0,
    unsupported: idx.FILE_TYPE_NOT_SUPPORTED ?? 0,
    inProgress: idx.IN_PROGRESS ?? 0,
    notStarted: idx.NOT_STARTED ?? 0,
    autoIndexOff: idx.AUTO_INDEX_OFF ?? 0,
    queued: idx.QUEUED ?? 0,
    empty: idx.EMPTY ?? 0,
    passwordProtected: idx.PASSWORD_PROTECTED ?? 0,
  };
}
