/**
 * ADR-SCH-02 D2/D6 in a schedule's history: a firing says it ran on classic chat where that matters, and that the
 * cost cap did not apply when its schedule had one.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { toFiring, toSchedule } from '../api';
import { runnerWords, ScheduleRow } from '../components/schedule-row';
import { firingView, scheduleView } from './fixtures';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const get = vi.mocked(apiClient.get);
const HARNESS_ON = { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true };

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('reading a firing', () => {
  it('keeps its runner and whether the cost cap was ignored; an older server reads as unknown and not ignored', () => {
    expect(toFiring(firingView({ runner: 'classic', runId: null, costCapIgnored: true }))).toMatchObject({
      runner: 'classic',
      runId: null,
      conversationId: 'conv-1',
      costCapIgnored: true,
    });
    expect(toFiring(firingView())).toMatchObject({ runner: null, costCapIgnored: false });
    expect(toFiring(firingView({ runner: 'engine' }))?.runner).toBeNull();
  });

  it('says only what matters', () => {
    expect(runnerWords({ runner: 'classic', costCapIgnored: true }, false)).toBe('schedules.firing.classicCapIgnored');
    expect(runnerWords({ runner: 'classic', costCapIgnored: false }, true)).toBe('schedules.firing.classic');
    // Every run of a classic org is classic: saying so on each would be noise.
    expect(runnerWords({ runner: 'classic', costCapIgnored: false }, false)).toBeNull();
    expect(runnerWords({ runner: 'harness', costCapIgnored: false }, true)).toBeNull();
    expect(runnerWords({ runner: null, costCapIgnored: false }, true)).toBeNull();
  });
});

describe('the history', () => {
  it('a classic run of a schedule with a cost cap links its conversation and says the cap did not apply', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_SCHEDULED_RUNS: true }, agentHarness: HARNESS_ON });
    get.mockResolvedValue({
      data: { firings: [firingView({ runner: 'classic', runId: null, conversationId: 'conv-7', costCapIgnored: true })] },
    });
    const schedule = toSchedule(scheduleView());
    if (schedule === null) throw new Error('not listed');
    render(
      <Theme>
        <ScheduleRow schedule={schedule} agentName="Support agent" orgView={false} onAction={async () => {}} />
      </Theme>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect((await screen.findByTestId('firing-runner')).textContent).toBe("Ran on classic chat; the cost cap doesn't apply");
    expect(screen.getByRole('link', { name: 'Open the conversation' }).getAttribute('href')).toContain('conversationId=conv-7');
  });
});
