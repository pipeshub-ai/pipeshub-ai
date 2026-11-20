import { describe, it, expect } from 'vitest';
import { lastRunLines } from '../last-run-summary';
import enUS from '@/lib/i18n/locales/en-US.json';

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

  it('has a line for every reason a run stops, including a run whose start could not be saved', () => {
    const lastRun = enUS.workspace.connectors.overview.lastRun as Record<string, unknown>;
    const reasons = ['max_pages', 'time_limit', 'stalled', 'rate_limited', 'start_not_recorded'] as const;
    for (const stopReason of reasons) {
      const [line] = lastRunLines({ stopReason });
      const [group, reason] = line.key.split('.');
      expect((lastRun[group] as Record<string, string>)[reason], line.key).toBeTruthy();
    }
  });
});
