/**
 * ADR-SCH-01 D1: a one-off schedule is listed ("Once, …"), and once it ran it says so and offers neither Resume nor
 * Run now (Delete stays). The schedule drawer's "Once" sends `{kind: once, at, timeZone}`, a minute to a year ahead.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { SchedulesApi, toSchedule } from '../api';
import { isSystemStatus } from '../copy';
import { ScheduleRow } from '../components/schedule-row';
import {
  browserTimeZone,
  checkedSchedule,
  ScheduleDrawer,
  emptyForm,
  localInputValue,
  onceInstant,
  toNewSchedule,
} from '../components/schedule-drawer';
import type { NewSchedule, Schedule } from '../types';
import { scheduleView } from './fixtures';

// The browser's zone, so a DST gap is a fixed date.
process.env.TZ = 'America/Los_Angeles';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);

const ONCE = { kind: 'once', at: '2026-11-26T23:47:00.000Z', timeZone: 'America/Los_Angeles' };

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView = () => {};
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.releasePointerCapture = () => {};
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const once = (over: Record<string, unknown> = {}): Schedule => {
  const schedule = toSchedule(scheduleView({ trigger: ONCE, ...over }));
  if (schedule === null) throw new Error('not listed');
  return schedule;
};

const renderRow = (schedule: Schedule) =>
  render(
    <Theme>
      <ScheduleRow schedule={schedule} agentName="Support agent" orgView={false} onAction={async () => {}} />
    </Theme>,
  );

describe('a one-off schedule on the list', () => {
  it('is listed, not dropped as an unknown trigger', async () => {
    get.mockResolvedValueOnce({ data: { schedules: [scheduleView({ trigger: ONCE }), scheduleView({ id: 'sch-2' })] } });
    const list = await SchedulesApi.list('mine');
    expect(list.map((s) => s.id)).toEqual(['sch-1', 'sch-2']);
    expect(list[0].trigger).toEqual(ONCE);
    // A once with no readable instant is not a schedule the page can say.
    expect(toSchedule(scheduleView({ trigger: { kind: 'once', at: 'soon', timeZone: 'UTC' } }))).toBeNull();
  });

  it('an active one says when it runs and offers Run now', () => {
    renderRow(once());
    expect(screen.getByTestId('schedule-once').textContent).toMatch(/^Once, /);
    expect(screen.getByRole('button', { name: 'Run now' })).toBeTruthy();
  });

  it('once it ran: "Ran once", no Resume or Run now, Delete stays', () => {
    const fired = once({ status: 'disabled', statusReason: 'ran_once', nextFireAt: null });
    expect(isSystemStatus(fired.status, fired.statusReason)).toBe(false);
    renderRow(fired);
    const row = screen.getByRole('group', { name: 'Monday digest' });
    expect(within(row).getAllByText(/Ran once/).length).toBeGreaterThan(0);
    expect(within(row).queryByText(/Turned off by PipesHub/)).toBeNull();
    expect(within(row).queryByRole('button', { name: 'Resume' })).toBeNull();
    expect(within(row).queryByRole('button', { name: 'Run now' })).toBeNull();
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeTruthy();
  });
});

describe('creating a one-off', () => {
  const now = new Date('2026-11-26T12:00:00Z');

  it('accepts a minute to a year ahead, and nothing outside it', () => {
    expect(onceInstant('', now)).toEqual({ problem: 'missing' });
    expect(onceInstant(localInputValue(new Date(now.getTime() + 30_000)), now)).toEqual({ problem: 'tooSoon' });
    expect(onceInstant(localInputValue(new Date(now.getTime() + 367 * 86_400_000)), now)).toEqual({ problem: 'tooFar' });
    const inFive = new Date(now.getTime() + 5 * 60_000);
    expect(onceInstant(localInputValue(inFive), now)).toEqual({ at: inFive.toISOString() });
  });

  it('a time the clocks skip is refused, never moved an hour; ordinary and repeated times are kept', () => {
    const before = new Date('2026-12-01T12:00:00-08:00');
    expect(onceInstant('2027-03-14T02:30', before)).toEqual({ problem: 'notExisting' });
    expect(onceInstant('2027-03-14T02:00', before)).toEqual({ problem: 'notExisting' });
    expect(onceInstant('2027-03-14T01:59', before)).toEqual({ at: '2027-03-14T09:59:00.000Z' });
    expect(onceInstant('2027-03-14T03:00', before)).toEqual({ at: '2027-03-14T10:00:00.000Z' });
    expect(onceInstant('2027-03-15T09:00', before)).toEqual({ at: '2027-03-15T16:00:00.000Z' });
    // 01:30 on 1 November 2026 happens twice; it exists, so it is kept.
    expect('at' in onceInstant('2026-11-01T01:30', new Date('2026-10-01T00:00:00Z'))).toBe(true);
  });

  it('the form is a once body in the browser’s zone, without cron fields', () => {
    const inFive = new Date(now.getTime() + 5 * 60_000);
    const body = toNewSchedule({ ...emptyForm(), name: 'Joke', query: 'Tell me a joke', kind: 'once', onceAt: localInputValue(inFive) }, now);
    expect(body.trigger).toEqual({ kind: 'once', at: inFive.toISOString(), timeZone: browserTimeZone() });
  });

  it('the route gets {kind: once, at, timeZone}', async () => {
    post.mockResolvedValueOnce({ data: { schedule: scheduleView({ trigger: ONCE }) } });
    const created = await SchedulesApi.create({
      name: 'Joke',
      agentKey: null,
      query: 'Tell me a joke',
      trigger: { kind: 'once', at: ONCE.at, timeZone: ONCE.timeZone },
      maxCostUsd: null,
      deadlineS: null,
      overlap: 'skip',
      notify: ['in_app'],
    });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/agent-harness/schedules',
      expect.objectContaining({ trigger: { kind: 'once', at: ONCE.at, timeZone: ONCE.timeZone } }),
      expect.anything(),
    );
    expect(created.schedule.trigger).toEqual(ONCE);
  });

  it('the time is checked again at submit: one that is no longer a minute ahead is not sent', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      vi.setSystemTime(new Date('2026-12-01T10:00:00-08:00'));
      get.mockResolvedValue({ data: { channels: [{ channel: 'in_app', available: true, reason: null }] } });
      const onSubmit = vi.fn(async (_input: NewSchedule) => {});
      render(
        <Theme>
          <ScheduleDrawer open onOpenChange={() => {}} agents={[]} onSubmit={onSubmit} />
        </Theme>,
      );
      const dialog = await screen.findByRole('dialog');
      fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Joke' } });
      fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Tell me a joke' } });
      fireEvent.click(within(dialog).getByRole('radio', { name: 'Once, at a date and time' }));
      const field = within(dialog).getByLabelText('Date and time');
      fireEvent.change(field, { target: { value: '2026-12-01T10:02' } });
      expect(within(dialog).queryByRole('alert')).toBeNull();
      vi.setSystemTime(new Date('2026-12-01T10:01:30-08:00'));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      expect(within(dialog).getByRole('alert').textContent).toBe('Pick a time at least a minute from now.');
      expect(onSubmit).not.toHaveBeenCalled();
      // A time still far enough ahead goes as picked.
      fireEvent.change(field, { target: { value: '2026-12-01T10:05' } });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0][0].trigger).toEqual({ kind: 'once', at: '2026-12-01T18:05:00.000Z', timeZone: browserTimeZone() });
    } finally {
      vi.useRealTimers();
    }
  });

  it('the upper bound follows the clock, not the moment the drawer opened', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      vi.setSystemTime(new Date('2026-12-01T10:00:00-08:00'));
      get.mockResolvedValue({ data: { channels: [{ channel: 'in_app', available: true, reason: null }] } });
      const onSubmit = vi.fn(async (_input: NewSchedule) => {});
      render(
        <Theme>
          <ScheduleDrawer open onOpenChange={() => {}} agents={[]} onSubmit={onSubmit} />
        </Theme>,
      );
      const dialog = await screen.findByRole('dialog');
      fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Joke' } });
      fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Tell me a joke' } });
      fireEvent.click(within(dialog).getByRole('radio', { name: 'Once, at a date and time' }));
      const field = within(dialog).getByLabelText('Date and time');
      // No native bound fixed at opening can disagree with the check (the browser would block Create on it).
      expect(field.hasAttribute('max')).toBe(false);
      expect(field.hasAttribute('min')).toBe(false);
      vi.setSystemTime(new Date('2026-12-01T10:10:00-08:00'));
      // 366 days from opening was 10:00; from now it is 10:10.
      fireEvent.change(field, { target: { value: '2027-12-02T10:05' } });
      expect(within(dialog).queryByRole('alert')).toBeNull();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0][0].trigger).toMatchObject({ kind: 'once', at: '2027-12-02T18:05:00.000Z' });
      // Past 366 days from now is still refused.
      fireEvent.change(field, { target: { value: '2027-12-02T10:11' } });
      expect(within(dialog).getByRole('alert').textContent).toBe('Pick a time at most a year ahead.');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      expect(onSubmit).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('a form whose one-off time is not valid never becomes a body with a blank time', () => {
    const stale = { ...emptyForm(), name: 'Joke', query: 'Tell me a joke', kind: 'once' as const, onceAt: '2026-12-01T10:02' };
    expect(checkedSchedule(stale, new Date('2026-12-01T10:01:30-08:00'))).toEqual({ problem: 'tooSoon' });
    expect(() => toNewSchedule(stale, new Date('2026-12-01T10:01:30-08:00'))).toThrow();
  });

  it('the drawer’s "Once" option sends the picked time', async () => {
    get.mockResolvedValue({ data: { channels: [{ channel: 'in_app', available: true, reason: null }] } });
    const onSubmit = vi.fn(async (_input: NewSchedule) => {});
    render(
      <Theme>
        <ScheduleDrawer open onOpenChange={() => {}} agents={[]} onSubmit={onSubmit} />
      </Theme>,
    );
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Joke' } });
    fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Tell me a joke' } });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Once, at a date and time' }));
    expect(within(dialog).queryByLabelText('Cron expression')).toBeNull();
    const field = within(dialog).getByLabelText('Date and time');
    fireEvent.change(field, { target: { value: localInputValue(new Date(Date.now() + 10_000)) } });
    expect(within(dialog).getByRole('alert').textContent).toBe('Pick a time at least a minute from now.');
    fireEvent.change(field, { target: { value: '2027-03-14T02:30' } });
    expect(within(dialog).getByRole('alert').textContent).toBe(
      "That time doesn't exist on that day in your time zone: the clocks skip it. Pick another time.",
    );
    expect((within(dialog).getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true);
    const later = new Date(Date.now() + 2 * 3_600_000);
    fireEvent.change(field, { target: { value: localInputValue(later) } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const sent = onSubmit.mock.calls[0][0];
    later.setSeconds(0, 0);
    expect(sent.trigger).toEqual({ kind: 'once', at: later.toISOString(), timeZone: browserTimeZone() });
  });
});
