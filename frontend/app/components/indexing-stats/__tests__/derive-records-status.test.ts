import { describe, expect, it } from 'vitest';
import { deriveClassificationStatus } from '../derive-records-status';
import type {
  ConnectorStatsResponse,
  ExtractionStatusCounts,
} from '@/app/(main)/workspace/connectors/types';

function statsWith(extractionStatus?: ExtractionStatusCounts): ConnectorStatsResponse['data'] {
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
