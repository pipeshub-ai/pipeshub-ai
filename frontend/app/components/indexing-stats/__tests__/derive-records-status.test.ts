import { describe, expect, it } from 'vitest';
import {
  deriveClassificationStatus,
  deriveEntitiesStatus,
  deriveRecordsStatus,
  stageApplicableCount,
} from '../derive-records-status';
import type {
  ConnectorStatsResponse,
  ExtractionStatusCounts,
} from '@/app/(main)/workspace/connectors/types';

function statsWith(
  extractionStatus?: ExtractionStatusCounts,
  entitiesStatus?: ExtractionStatusCounts
): ConnectorStatsResponse['data'] {
  return {
    orgId: 'org-1',
    connectorId: 'conn-1',
    origin: 'CONNECTOR',
    stats: {
      total: 10,
      indexingStatus: {
        NOT_STARTED: 0,
        IN_PROGRESS: 0,
        COMPLETED: 10,
        FAILED: 0,
        FILE_TYPE_NOT_SUPPORTED: 0,
        AUTO_INDEX_OFF: 0,
        ENABLE_MULTIMODAL_MODELS: 0,
        EMPTY: 0,
        QUEUED: 0,
        PAUSED: 0,
      },
      ...(extractionStatus ? { extractionStatus } : {}),
      ...(entitiesStatus ? { entitiesStatus } : {}),
    },
    byRecordType: [],
  };
}

describe('deriveClassificationStatus', () => {
  it('returns null when the backend sends no extractionStatus', () => {
    expect(deriveClassificationStatus(statsWith())).toBeNull();
    expect(deriveClassificationStatus(null)).toBeNull();
  });

  it('maps classify-stage counts and defaults missing statuses to 0', () => {
    expect(deriveClassificationStatus(statsWith({ COMPLETED: 6, FAILED: 3 }))).toEqual({
      completed: 6,
      waiting: 0,
      failed: 3,
      skipped: 0,
    });
  });

  it('counts queued, running and paused records as waiting', () => {
    expect(
      deriveClassificationStatus(statsWith({ QUEUED: 4, IN_PROGRESS: 2, PAUSED: 1 }))?.waiting
    ).toBe(7);
  });

  it('leaves out records that have not reached the stage', () => {
    const progress = deriveClassificationStatus(statsWith({ COMPLETED: 2, NOT_STARTED: 40 }));
    expect(progress).toEqual({ completed: 2, waiting: 0, failed: 0, skipped: 0 });
  });
});

describe('stageApplicableCount', () => {
  it('counts completed, waiting and failed records but not skipped ones', () => {
    expect(stageApplicableCount({ completed: 5, waiting: 3, failed: 2, skipped: 9 })).toBe(10);
  });
});

describe('deriveEntitiesStatus', () => {
  it('returns null when the backend sends no entitiesStatus', () => {
    expect(deriveEntitiesStatus(statsWith({ FAILED: 2 }))).toBeNull();
    expect(deriveEntitiesStatus(null)).toBeNull();
  });

  it('maps entities-stage counts and defaults missing statuses to 0', () => {
    expect(deriveEntitiesStatus(statsWith(undefined, { COMPLETED: 4, FAILED: 5, SKIPPED: 1 }))).toEqual({
      completed: 4,
      waiting: 0,
      failed: 5,
      skipped: 1,
    });
  });

  it('reads the entities stage, not classification', () => {
    expect(deriveEntitiesStatus(statsWith({ FAILED: 9 }, { FAILED: 2 }))?.failed).toBe(2);
  });
});

describe('deriveRecordsStatus', () => {
  it('counts password-protected records in their own bucket, not as failed', () => {
    const stats = statsWith();
    stats.stats.indexingStatus = { ...stats.stats.indexingStatus, FAILED: 2, PASSWORD_PROTECTED: 3 };

    const status = deriveRecordsStatus(stats);

    expect(status.passwordProtected).toBe(3);
    expect(status.failed).toBe(2);
  });

  it('reads 0 from a backend that predates the status', () => {
    expect(deriveRecordsStatus(statsWith()).passwordProtected).toBe(0);
    expect(deriveRecordsStatus(null).passwordProtected).toBe(0);
  });
});
