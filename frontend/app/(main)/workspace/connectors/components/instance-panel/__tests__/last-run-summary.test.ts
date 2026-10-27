import { describe, it, expect } from 'vitest';
import { lastRunLines } from '../last-run-summary';

describe('lastRunLines', () => {
  it('lists why the run stopped first, then the counts that are not zero', () => {
    expect(
      lastRunLines({ pagesSaved: 40, failed: 0, robotsSkipped: 3, stopReason: 'max_pages', resumed: true }),
    ).toEqual([
      { key: 'stopReason.max_pages' },
      { key: 'resumed' },
      { key: 'pagesSaved', count: 40 },
      { key: 'robotsSkipped', count: 3 },
    ]);
  });

  it('shows nothing for a connector that reports no summary', () => {
    expect(lastRunLines(null)).toEqual([]);
    expect(lastRunLines({})).toEqual([]);
  });
});
