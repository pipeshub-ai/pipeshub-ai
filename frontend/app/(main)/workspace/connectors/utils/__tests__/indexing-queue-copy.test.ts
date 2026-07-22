import { describe, expect, it } from 'vitest';
import {
  describeIndexingQueueDetail,
  formatQueueEta,
  shouldShowIndexingQueue,
} from '../indexing-queue-copy';

describe('shouldShowIndexingQueue', () => {
  it('shows only while indexing with meaningful lag', () => {
    expect(shouldShowIndexingQueue({ lag: 100, pending: 0 }, { indexing: true })).toBe(true);
    expect(shouldShowIndexingQueue({ lag: 5, pending: 0 }, { indexing: true })).toBe(false);
    expect(shouldShowIndexingQueue({ lag: 100, pending: 0 }, { indexing: false })).toBe(false);
  });
});

describe('formatQueueEta', () => {
  it('returns null when ETA is unknown', () => {
    expect(formatQueueEta(null)).toBeNull();
    expect(formatQueueEta(undefined)).toBeNull();
  });

  it('bands longer waits instead of exact minutes', () => {
    const copy = formatQueueEta(20 * 60);
    expect(copy?.params).toMatchObject({ low: 15, high: 45 });
  });
});

describe('describeIndexingQueueDetail', () => {
  it('includes jobs-ahead count and optional ETA', () => {
    const detail = describeIndexingQueueDetail({ lag: 3100, pending: 40, etaSeconds: 1200 });
    expect(detail.jobs.params.count).toBe(3100);
    expect(detail.eta?.params).toMatchObject({ low: 15, high: 45 });
  });
});
