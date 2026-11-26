/**
 * C20.53 / G-52 (WP100-LANG-F1): a schedule's runs fire with no browser, so the request that makes the schedule
 * names the person's PipesHub language (`Accept-Language`, as a chat turn does) and Node pins it for every firing.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { uiLanguageHeader } from '@/lib/api/ui-language';
import { SchedulesApi } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/i18n', () => ({ i18n: { language: 'de-DE' } }));

const post = vi.mocked(apiClient.post);

const view = {
  id: 'sch-1',
  name: 'Monday digest',
  actorId: 'u-ana',
  agentKey: 'agent-a',
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'skip' },
  maxCostUsd: null,
  deadlineS: 28800,
  status: 'active',
  statusReason: null,
  nextFireAt: '2026-10-12T07:00:00.000Z',
  createdAt: '2026-10-06T10:00:00.000Z',
  updatedAt: '2026-10-06T10:00:00.000Z',
};

afterEach(() => vi.clearAllMocks());

describe('WP100-LANG-F1: a new schedule names the UI language', () => {
  it('sends Accept-Language with the create request, and the language is the UI one', async () => {
    post.mockResolvedValueOnce({ data: { schedule: view } });
    await SchedulesApi.create({
      name: 'Monday digest',
      agentKey: 'agent-a',
      query: 'Summarise new tickets',
      trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'skip' },
      maxCostUsd: null,
      deadlineS: null,
      overlap: 'skip',
      notify: [],
    });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/agent-harness/schedules',
      expect.anything(),
      expect.objectContaining({ headers: { 'Accept-Language': 'de-DE' }, suppressErrorToast: true }),
    );
  });

  it('names none without a UI language', () => {
    expect(uiLanguageHeader()).toEqual({ 'Accept-Language': 'de-DE' });
    (i18n as { language: string }).language = '';
    expect(uiLanguageHeader()).toEqual({});
  });
});
