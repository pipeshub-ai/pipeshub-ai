import { describe, expect, it } from 'vitest';
import { isRateLimitPause } from '../rate-limit-pause';

const PAUSED =
  'Engineering GitHub is limiting how often PipesHub can read from it, so indexing this ' +
  'file is paused. It will retry automatically after 10:35 UTC.';
const RETRY_SCHEDULED = 'Indexing hit a temporary problem and will retry automatically.';

describe('isRateLimitPause', () => {
  it('shows the pause reason of a queued record, from its wording', () => {
    expect(isRateLimitPause('QUEUED', PAUSED)).toBe(true);
  });

  it('shows a queued record’s reason while it carries a not-before time', () => {
    expect(isRateLimitPause('QUEUED', 'Paused until 10:35 UTC.', 1_800_000_000_000)).toBe(true);
  });

  it('keeps the generic retry text of a transient requeue hidden', () => {
    expect(isRateLimitPause('QUEUED', RETRY_SCHEDULED)).toBe(false);
    expect(isRateLimitPause('QUEUED', RETRY_SCHEDULED, null)).toBe(false);
  });

  it('is only about queued records with a reason', () => {
    expect(isRateLimitPause('FAILED', PAUSED)).toBe(false);
    expect(isRateLimitPause('COMPLETED', PAUSED, 1)).toBe(false);
    expect(isRateLimitPause('QUEUED', '', 1)).toBe(false);
    expect(isRateLimitPause('QUEUED', undefined)).toBe(false);
  });
});
