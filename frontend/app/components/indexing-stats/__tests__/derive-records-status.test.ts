import { describe, expect, it } from 'vitest';
import { deriveClassificationStatus, deriveEntitiesStatus } from '../derive-records-status';
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
    expect(deriveClassificationStatus(statsWith({ COMPLETED: 6, FAILED: 3, IN_PROGRESS: 1 }))).toEqual({
      completed: 6,
      skipped: 0,
      failed: 3,
      queued: 0,
    });
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
      skipped: 1,
      failed: 5,
      queued: 0,
    });
  });

  it('reads the entities stage, not classification', () => {
    expect(deriveEntitiesStatus(statsWith({ FAILED: 9 }, { FAILED: 2 }))?.failed).toBe(2);
  });
});
