import { describe, it, expect } from 'vitest';
import {
  STALL_THRESHOLD_MS,
  getIndexingProgressView,
  hasActiveIndexing,
  isActiveIndexingStatus,
} from '../indexing-progress';

const NOW = 1_000_000_000_000;

describe('isActiveIndexingStatus', () => {
  it('treats QUEUED and IN_PROGRESS as active', () => {
    expect(isActiveIndexingStatus('QUEUED')).toBe(true);
    expect(isActiveIndexingStatus('IN_PROGRESS')).toBe(true);
  });

  it('treats terminal statuses as inactive', () => {
    for (const s of ['COMPLETED', 'FAILED', 'EMPTY', 'AUTO_INDEX_OFF', 'NOT_STARTED', 'FILE_TYPE_NOT_SUPPORTED']) {
      expect(isActiveIndexingStatus(s)).toBe(false);
    }
    expect(isActiveIndexingStatus(null)).toBe(false);
    expect(isActiveIndexingStatus(undefined)).toBe(false);
  });
});

describe('hasActiveIndexing', () => {
  it('returns true when any record is in flight', () => {
    expect(
      hasActiveIndexing([
        { indexingStatus: 'COMPLETED' },
        { indexingStatus: 'IN_PROGRESS' },
      ]),
    ).toBe(true);
  });

  it('returns false when every record is settled', () => {
    expect(
      hasActiveIndexing([
        { indexingStatus: 'COMPLETED' },
        { indexingStatus: 'FAILED' },
        { indexingStatus: null },
      ]),
    ).toBe(false);
  });

  it('returns false for an empty list', () => {
    expect(hasActiveIndexing([])).toBe(false);
  });
});

describe('getIndexingProgressView', () => {
  it('reports QUEUED records as active with a queued label', () => {
    const view = getIndexingProgressView({ indexingStatus: 'QUEUED' }, NOW);
    expect(view.isActive).toBe(true);
    expect(view.isStalled).toBe(false);
    expect(view.label).toBe('Queued');
  });

  it('labels the EXTRACTING stage while in progress', () => {
    const view = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'EXTRACTING', lastActivityTimestamp: NOW },
      NOW,
    );
    expect(view.label).toBe('Extracting content');
    expect(view.isStalled).toBe(false);
    expect(view.percent).toBeGreaterThan(0);
    expect(view.percent).toBeLessThan(100);
  });

  it('advances percent from EXTRACTING to INDEXING', () => {
    const extracting = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'EXTRACTING', lastActivityTimestamp: NOW },
      NOW,
    );
    const indexing = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'INDEXING', lastActivityTimestamp: NOW },
      NOW,
    );
    expect(indexing.percent).toBeGreaterThan(extracting.percent);
    expect(indexing.label).toBe('Indexing');
  });

  it('falls back to a generic label when stage is missing (any file type/model)', () => {
    const view = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: null, lastActivityTimestamp: NOW },
      NOW,
    );
    expect(view.label).toBe('Processing');
    expect(view.isActive).toBe(true);
  });

  it('flags a stalled record when the heartbeat is stale', () => {
    const view = getIndexingProgressView(
      {
        indexingStatus: 'IN_PROGRESS',
        indexingStage: 'EXTRACTING',
        lastActivityTimestamp: NOW - STALL_THRESHOLD_MS - 1,
        reason: null,
      },
      NOW,
    );
    expect(view.isStalled).toBe(true);
    expect(view.label).toBe('Stalled');
    expect(view.detail).toContain('No recent progress');
  });

  it('surfaces the backend reason when a stalled record has one', () => {
    const view = getIndexingProgressView(
      {
        indexingStatus: 'IN_PROGRESS',
        indexingStage: 'EXTRACTING',
        lastActivityTimestamp: NOW - STALL_THRESHOLD_MS - 1,
        reason: 'Rate limited by provider',
      },
      NOW,
    );
    expect(view.isStalled).toBe(true);
    expect(view.detail).toBe('Rate limited by provider');
  });

  it('does not flag stall when heartbeat is recent', () => {
    const view = getIndexingProgressView(
      {
        indexingStatus: 'IN_PROGRESS',
        indexingStage: 'INDEXING',
        lastActivityTimestamp: NOW - 1000,
      },
      NOW,
    );
    expect(view.isStalled).toBe(false);
  });

  it('does not flag stall while still QUEUED, regardless of heartbeat', () => {
    const view = getIndexingProgressView(
      { indexingStatus: 'QUEUED', lastActivityTimestamp: NOW - STALL_THRESHOLD_MS - 1 },
      NOW,
    );
    expect(view.isStalled).toBe(false);
  });

  it('ignores a stale stage on terminal records', () => {
    const view = getIndexingProgressView(
      { indexingStatus: 'COMPLETED', indexingStage: 'EXTRACTING', lastActivityTimestamp: NOW - STALL_THRESHOLD_MS - 1 },
      NOW,
    );
    expect(view.isActive).toBe(false);
    expect(view.isStalled).toBe(false);
    expect(view.percent).toBe(100);
  });
});
