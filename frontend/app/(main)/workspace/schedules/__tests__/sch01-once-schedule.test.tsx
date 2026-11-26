/**
 * ADR-SCH-01 D1: a one-off schedule is listed ("Once, …"), and once it ran it says so and offers neither Resume nor
 * Run now (Delete stays). The create dialog's "Once" sends `{kind: once, at, timeZone}`, a minute to a year ahead.
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
  CreateScheduleDialog,
  emptyForm,
  localInputValue,
  onceInstant,
  toNewSchedule,
} from '../components/create-schedule-dialog';
import type { NewSchedule, Schedule } from '../types';
import { scheduleView } from './fixtures';

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

  it('the dialog’s "Once" option sends the picked time', async () => {
    get.mockResolvedValue({ data: { channels: [{ channel: 'in_app', available: true, reason: null }] } });
    const onSubmit = vi.fn(async (_input: NewSchedule) => {});
    render(
      <Theme>
        <CreateScheduleDialog open onOpenChange={() => {}} agents={[]} onSubmit={onSubmit} />
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
    const later = new Date(Date.now() + 2 * 3_600_000);
    fireEvent.change(field, { target: { value: localInputValue(later) } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const sent = onSubmit.mock.calls[0][0];
    later.setSeconds(0, 0);
    expect(sent.trigger).toEqual({ kind: 'once', at: later.toISOString(), timeZone: browserTimeZone() });
  });
});
