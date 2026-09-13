'use client';

import type { ConnectorStatsResponse, RecordsStatus } from '@/app/(main)/workspace/connectors/types';

export interface ClassificationStatus {
  completed: number;
  skipped: number;
  failed: number;
  queued: number;
}

/** Null when the backend predates pipeline stages and sends no extractionStatus. */
export function deriveClassificationStatus(
  stats?: ConnectorStatsResponse['data'] | null
): ClassificationStatus | null {
  const ext = stats?.stats?.extractionStatus;
  if (!ext) return null;
  return {
    completed: ext.COMPLETED ?? 0,
    skipped: ext.SKIPPED ?? 0,
    failed: ext.FAILED ?? 0,
    queued: ext.QUEUED ?? 0,
  };
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
  };
}
