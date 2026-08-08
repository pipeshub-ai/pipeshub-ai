import { describe, expect, it } from 'vitest';
import {
  describeIndexingQueueDetail,
  formatQueueEta,
  indexingQueueBacklog,
  shouldShowIndexingQueue,
} from '../indexing-queue-copy';

describe('shouldShowIndexingQueue', () => {
  it('shows while indexing with meaningful undelivered lag', () => {
    expect(shouldShowIndexingQueue({ lag: 100, pending: 0 }, { indexing: true })).toBe(true);
    expect(shouldShowIndexingQueue({ lag: 5, pending: 0 }, { indexing: true })).toBe(false);
    expect(shouldShowIndexingQueue({ lag: 100, pending: 0 }, { indexing: false })).toBe(false);
  });

  it('treats pending as additive for API compat (backend sends pending=0)', () => {
    expect(shouldShowIndexingQueue({ lag: 0, pending: 40 }, { indexing: true })).toBe(true);
    expect(shouldShowIndexingQueue({ lag: 0, pending: 5 }, { indexing: true })).toBe(false);
  });
});

describe('indexingQueueBacklog', () => {
  it('sums lag and pending', () => {
    expect(indexingQueueBacklog({ lag: 10, pending: 0 })).toBe(10);
    expect(indexingQueueBacklog({ lag: 10, pending: 40 })).toBe(50);
    expect(indexingQueueBacklog(null)).toBe(0);
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
  it('includes org backlog count and optional ETA', () => {
    const detail = describeIndexingQueueDetail({ lag: 3140, pending: 0, etaSeconds: 1200 });
    expect(detail.jobs.params.count).toBe(3140);
    expect(detail.jobs.text).toContain('your organization');
    expect(detail.jobs.text).not.toContain('shared');
    expect(detail.eta?.params).toMatchObject({ low: 15, high: 45 });
  });
});
