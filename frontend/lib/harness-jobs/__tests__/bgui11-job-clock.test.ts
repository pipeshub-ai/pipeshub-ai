/**
 * BGUI-11: one clock for a job's time on its card, report, panel header and the lists. It read only minutes and
 * seconds, so a job five weeks old said "Working · 54614m 26s" (BGUI-10's shot); past an hour it says hours and
 * minutes, past a day the days. Each boundary, in the words a person reads.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { jobClock } from '../jobs';
import { clockWords } from '@/app/(main)/chat/components/message-area/job-card/job-card-state';
import { formatDuration } from '@/app/(main)/chat/components/message-area/agent-activity';

const t = (key: string, vars?: Record<string, unknown>) => i18n.t(key, vars) as string;
const S = 1000;
const M = 60 * S;
const H = 60 * M;
const D = 24 * H;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('BGUI-11: the job clock', () => {
  it.each([
    [0, '0s'],
    [59 * S, '59s'],
    [59 * S + 499, '59s'],
    [60 * S, '1m 00s'],
    [6 * M + 27 * S, '6m 27s'],
    [59 * M + 59 * S, '59m 59s'],
    [H, '1h 0m'],
    [H + 12 * M + 40 * S, '1h 12m'],
    [23 * H + 59 * M + 59 * S, '23h 59m'],
    [D, '1 day'],
    [D + 23 * H, '1 day'],
    [2 * D, '2 days'],
    [54_614 * M + 26 * S, '37 days'],
  ])('%i ms reads "%s"', (ms, words) => {
    expect(jobClock(ms, t)).toBe(words);
  });

  it('a negative time is no time', () => {
    expect(jobClock(-5 * S, t)).toBe('0s');
  });

  it('the card and the report read the same clock as the panel and the lists', () => {
    for (const ms of [8 * S, 2 * M + 14 * S, 3 * H + 5 * M, 5 * D]) expect(clockWords(ms, i18n.t.bind(i18n))).toBe(jobClock(ms, t));
  });

  it('a step or run that took over an hour reads the same hours and days; shorter ones keep their own words', () => {
    const tf = i18n.t.bind(i18n);
    expect(formatDuration(2 * M + 5 * S, tf)).toBe('2m 5s');
    expect(formatDuration(H + 12 * M, tf)).toBe('1h 12m');
    expect(formatDuration(54_614 * M, tf)).toBe('37 days');
  });

  it('every locale has the hours and the days', async () => {
    for (const lng of ['de-DE', 'cs-CZ', 'ko-KR', 'zh-CN']) {
      await i18n.changeLanguage(lng);
      expect(jobClock(H + 12 * M, t)).not.toContain('{{');
      expect(jobClock(3 * D, t)).toContain('3');
      expect(jobClock(3 * D, t)).not.toContain('ui.job');
    }
    await i18n.changeLanguage('en-US');
  });
});
