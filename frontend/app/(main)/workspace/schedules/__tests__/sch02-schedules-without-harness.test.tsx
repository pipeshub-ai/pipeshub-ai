/**
 * ADR-SCH-02: schedules are there wherever Labs `ENABLE_SCHEDULED_RUNS` is on, the agent harness or not (D1), and a
 * cost cap is offered only where the org's runs are durable; a kept one is said not to apply to classic runs (D6).
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { selectScheduledRunsEnabled, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { toSchedule } from '../api';
import { ScheduleRow } from '../components/schedule-row';
import { ScheduleDrawer } from '../components/schedule-drawer';
import type { NewSchedule, Schedule } from '../types';
import { scheduleView } from './fixtures';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = vi.mocked(apiClient.get);

const HARNESS_ON = { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true };
const CLASSIC = { useHarness: false, harnessMajor: 2, reason: 'org_disabled', resumeInFlightRuns: false, harnessOn: false };

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

beforeEach(() => {
  get.mockResolvedValue({ data: { channels: [{ channel: 'in_app', available: true, reason: null }] } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const onOrg = (agentHarness: typeof HARNESS_ON | typeof CLASSIC | null) =>
  useFeatureFlagsStore.setState({ flags: { ENABLE_SCHEDULED_RUNS: true }, agentHarness });

describe('D1: the gate is the Labs flag alone', () => {
  it('a classic org, a harness org and an install without the harness all have schedules with the flag on', () => {
    for (const agentHarness of [CLASSIC, HARNESS_ON, null]) {
      expect(selectScheduledRunsEnabled({ flags: { ENABLE_SCHEDULED_RUNS: true }, agentHarness } as never)).toBe(true);
    }
  });

  it('the flag off (or unread) is no schedules, whatever the harness', () => {
    for (const agentHarness of [CLASSIC, HARNESS_ON, null]) {
      expect(selectScheduledRunsEnabled({ flags: { ENABLE_SCHEDULED_RUNS: false }, agentHarness } as never)).toBe(false);
      expect(selectScheduledRunsEnabled({ flags: null, agentHarness } as never)).toBe(false);
    }
  });
});

describe('D6: the cost cap in the create form', () => {
  const fillAndCreate = async (onSubmit: (input: NewSchedule) => Promise<void>) => {
    render(
      <Theme>
        <ScheduleDrawer open onOpenChange={() => {}} agents={[]} onSubmit={onSubmit} />
      </Theme>,
    );
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Digest' } });
    fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Summarise' } });
    fireEvent.change(within(dialog).getByLabelText('Cron expression'), { target: { value: '0 9 * * 1' } });
    return dialog;
  };

  it('is offered and sent where the org runs on the harness', async () => {
    onOrg(HARNESS_ON);
    const onSubmit = vi.fn(async () => {});
    const dialog = await fillAndCreate(onSubmit);
    fireEvent.change(within(dialog).getByLabelText('Budget per run, USD (optional)'), { target: { value: '1.5' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ maxCostUsd: 1.5 });
  });

  it('is not offered, and never sent, on a classic org', async () => {
    onOrg(CLASSIC);
    const onSubmit = vi.fn(async () => {});
    const dialog = await fillAndCreate(onSubmit);
    expect(within(dialog).queryByLabelText('Budget per run, USD (optional)')).toBeNull();
    // The rest of the form is the same on both.
    expect(within(dialog).getByLabelText('Deadline, hours (optional)')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ maxCostUsd: null });
  });
});

describe('D6: a schedule’s cost cap on its row', () => {
  const listed = (over: Record<string, unknown> = {}): Schedule => {
    const schedule = toSchedule(scheduleView(over));
    if (schedule === null) throw new Error('not listed');
    return schedule;
  };
  const renderRow = (schedule: Schedule) =>
    render(
      <Theme>
        <ScheduleRow schedule={schedule} agentName="Support agent" orgView={false} onAction={async () => {}} />
      </Theme>,
    );

  it('a harness org reads the cap as the run’s budget', () => {
    onOrg(HARNESS_ON);
    renderRow(listed({ maxCostUsd: 2 }));
    expect(screen.getByText(/Up to 2 USD per run/)).toBeTruthy();
    expect(screen.queryByTestId('schedule-cap-not-applied')).toBeNull();
  });

  it('a classic org is told a kept cap does not apply, and no budget is claimed', () => {
    onOrg(CLASSIC);
    renderRow(listed({ maxCostUsd: 2 }));
    expect(screen.getByTestId('schedule-cap-not-applied').textContent).toBe(
      "Its cost cap of 2 USD doesn't apply: your organization's runs use the classic chat, which has no cost cap.",
    );
    expect(screen.queryByText(/Up to 2 USD per run/)).toBeNull();
    expect(screen.queryByText(/Default budget per run/)).toBeNull();
  });

  it('a classic org with no cap says nothing about one', () => {
    onOrg(CLASSIC);
    renderRow(listed({ maxCostUsd: null }));
    expect(screen.queryByTestId('schedule-cap-not-applied')).toBeNull();
    expect(screen.queryByText(/budget/i)).toBeNull();
  });
});
