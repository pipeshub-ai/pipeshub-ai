/** U4 SA-1..SA-4: the schedules client maps P8a's routes field by field. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { SchedulesApi, toSchedule } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);
const del = vi.mocked(apiClient.delete);

export const scheduleView = (over: Record<string, unknown> = {}) => ({
  id: 'sch-1',
  name: 'Monday digest',
  actorId: 'u-ana',
  agentKey: 'agent-a',
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'skip' },
  maxCostUsd: 2,
  deadlineS: 28800,
  status: 'active',
  statusReason: null,
  nextFireAt: '2026-10-12T07:00:00.000Z',
  createdAt: '2026-10-06T10:00:00.000Z',
  updatedAt: '2026-10-06T10:00:00.000Z',
  input: { query: 'Summarise new tickets' },
  ...over,
});

afterEach(() => vi.clearAllMocks());

describe('SchedulesApi', () => {
  it('SA-1: lists by scope, picks fields, drops malformed entries, keeps the prompt only when sent', async () => {
    get.mockResolvedValueOnce({
      data: {
        schedules: [
          { ...scheduleView(), webhookTokenHash: 'secret-hash', extra: 1 },
          scheduleView({ id: 'sch-2', input: undefined }),
          { id: 'broken' },
        ],
      },
    });
    const list = await SchedulesApi.list('mine');
    expect(get).toHaveBeenCalledWith('/api/v1/agent-harness/schedules', expect.objectContaining({ params: { scope: 'mine' }, suppressErrorToast: true }));
    expect(list.map((s) => s.id)).toEqual(['sch-1', 'sch-2']);
    expect(list[0].query).toBe('Summarise new tickets');
    expect(list[1].query).toBeNull();
    expect(JSON.stringify(list)).not.toContain('secret-hash');
    // A webhook made before signatures (no `auth`) takes the token (TR-04).
    expect(toSchedule({ ...scheduleView(), trigger: { kind: 'webhook', coalesceS: 300 } })?.trigger).toEqual({
      kind: 'webhook',
      coalesceS: 300,
      auth: 'token',
    });
    expect(toSchedule({ ...scheduleView(), trigger: { kind: 'webhook', coalesceS: 300, auth: 'signature' } })?.trigger).toEqual({
      kind: 'webhook',
      coalesceS: 300,
      auth: 'signature',
    });
    // A schedule from before WP-24 reads as the defaults: skip an overlap, tell in PipesHub.
    expect(list[0]).toMatchObject({ overlap: 'skip', notify: ['in_app'] });
    expect(toSchedule(scheduleView({ overlap: 'queue', notify: ['slack', 'email', 'fax'] }))).toMatchObject({
      overlap: 'queue',
      notify: ['email', 'slack'],
    });
  });

  it('SA-2: create sends the cron or webhook body and returns the secret once', async () => {
    post.mockResolvedValueOnce({ data: { schedule: scheduleView(), webhookToken: undefined } });
    await SchedulesApi.create({
      name: 'Monday digest',
      agentKey: 'agent-a',
      query: 'Summarise new tickets',
      trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'run_latest' },
      maxCostUsd: 2,
      deadlineS: 3600,
      overlap: 'queue',
      notify: ['in_app', 'email'],
    });
    expect(post).toHaveBeenLastCalledWith(
      '/api/v1/agent-harness/schedules',
      {
        name: 'Monday digest',
        agentKey: 'agent-a',
        input: { query: 'Summarise new tickets' },
        trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'run_latest' },
        maxCostUsd: 2,
        deadlineS: 3600,
        overlap: 'queue',
        notify: ['in_app', 'email'],
      },
      expect.objectContaining({ suppressErrorToast: true }),
    );

    post.mockResolvedValueOnce({
      data: { schedule: scheduleView({ trigger: { kind: 'webhook', coalesceS: 300 } }), webhookToken: 'tok-123' },
    });
    const created = await SchedulesApi.create({
      name: 'Hook',
      agentKey: null,
      query: 'Go',
      trigger: { kind: 'webhook', auth: 'token' },
      maxCostUsd: null,
      deadlineS: null,
      overlap: 'skip',
      notify: [],
    });
    expect(post.mock.calls[1][1]).toEqual({
      name: 'Hook',
      input: { query: 'Go' },
      trigger: { kind: 'webhook', auth: 'token' },
      overlap: 'skip',
      notify: [],
    });
    expect(created).toMatchObject({ webhookToken: 'tok-123', webhookSecret: null });

    // TR-04: a signed webhook (the default) answers its signing key once.
    post.mockResolvedValueOnce({
      data: { schedule: scheduleView({ trigger: { kind: 'webhook', coalesceS: 300, auth: 'signature' } }), webhookSecret: 'pxs_key' },
    });
    const signed = await SchedulesApi.create({
      name: 'Signed',
      agentKey: null,
      query: 'Go',
      trigger: { kind: 'webhook', auth: 'signature' },
      maxCostUsd: null,
      deadlineS: null,
      overlap: 'skip',
      notify: ['in_app'],
    });
    expect((post.mock.calls[2][1] as { trigger: unknown }).trigger).toEqual({ kind: 'webhook', auth: 'signature' });
    expect(signed).toMatchObject({ webhookToken: null, webhookSecret: 'pxs_key' });
  });

  it('SA-5 (TR-07): the notice channels a new schedule may use, per agent; malformed rows dropped', async () => {
    get.mockResolvedValueOnce({
      data: {
        channels: [
          { channel: 'in_app', available: true, reason: null },
          { channel: 'email', available: false, reason: 'org_off' },
          { channel: 'slack', available: false, reason: 'no_slack_bot' },
          { channel: 'fax', available: true, reason: null },
        ],
      },
    });
    expect(await SchedulesApi.noticeChannels('agent-a')).toEqual([
      { channel: 'in_app', available: true, reason: null },
      { channel: 'email', available: false, reason: 'org_off' },
      { channel: 'slack', available: false, reason: 'no_slack_bot' },
    ]);
    expect(get).toHaveBeenLastCalledWith(
      '/api/v1/agent-harness/schedules/notify-channels',
      expect.objectContaining({ params: { agentKey: 'agent-a' }, suppressErrorToast: true }),
    );
    get.mockResolvedValueOnce({ data: { channels: [] } });
    await SchedulesApi.noticeChannels(null);
    expect(get).toHaveBeenLastCalledWith('/api/v1/agent-harness/schedules/notify-channels', expect.objectContaining({ params: {} }));
  });

  it('SA-3: run now sends a new Idempotency-Key per call', async () => {
    const firing = { id: 'f1', kind: 'manual', slotAt: '2026-10-06T10:00:00.000Z', status: 'admitted', reason: null, runId: 'r1', conversationId: 's1', missed: 0, attempts: 1 };
    post.mockResolvedValue({ data: firing });
    await SchedulesApi.runNow('sch-1');
    await SchedulesApi.runNow('sch-1');
    const keys = post.mock.calls.map((call) => (call[2] as { headers: Record<string, string> }).headers['Idempotency-Key']);
    expect(post.mock.calls[0][0]).toBe('/api/v1/agent-harness/schedules/sch-1/run');
    expect(keys[0]).toMatch(/^[A-Za-z0-9._:~-]+$/);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it('SA-4: pause, resume, delete and firings use their routes quietly', async () => {
    post.mockResolvedValue({ data: scheduleView({ status: 'paused' }) });
    del.mockResolvedValue({ data: undefined });
    get.mockResolvedValue({ data: { firings: [] } });
    await SchedulesApi.pause('a b');
    await SchedulesApi.resume('sch-1');
    await SchedulesApi.remove('sch-1');
    await SchedulesApi.firings('sch-1');
    expect(post.mock.calls.map((c) => c[0])).toEqual([
      '/api/v1/agent-harness/schedules/a%20b/pause',
      '/api/v1/agent-harness/schedules/sch-1/resume',
    ]);
    expect(del).toHaveBeenCalledWith('/api/v1/agent-harness/schedules/sch-1', { suppressErrorToast: true });
    expect(get).toHaveBeenCalledWith('/api/v1/agent-harness/schedules/sch-1/firings', { suppressErrorToast: true });
  });
});
