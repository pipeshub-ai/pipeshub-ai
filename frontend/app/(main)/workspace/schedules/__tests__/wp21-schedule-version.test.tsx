/**
 * WP-21 (OWD-14), approved mock section 4: a schedule keeps the agent version it pinned until its owner moves it, or
 * follows every publish. The owner moves it from the schedule's row; an admin sees it read-only.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { nodeRefusal } from '../../harness-ui/__tests__/fake-refusal';
import { SchedulesApi, toSchedule } from '../api';
import { SchedulesPage } from '../components/schedules-page';
import { pinOf, versionChoices } from '../components/schedule-version';
import { emptyForm, toNewSchedule } from '../components/create-schedule-dialog';
import { useSchedulesStore } from '../store';
import { scheduleView } from './fixtures';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('../../approvals/directory', () => ({
  ApprovalsDirectory: {
    names: vi.fn(async () => ({ 'agent-a': 'Support triage', 'u-ana': 'Ana Admin', 'u-mia': 'Mia Member' })),
    searchAgents: vi.fn(async () => [{ id: 'agent-a', name: 'Support triage' }]),
  },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));

let profile = { isAdmin: false as boolean | null, isInitialized: true };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof profile) => unknown) => selector(profile),
  selectIsAdmin: (s: typeof profile) => s.isAdmin,
  selectIsProfileInitialized: (s: typeof profile) => s.isInitialized,
}));

const get = vi.mocked(apiClient.get);
const put = vi.mocked(apiClient.put);
const HARNESS_ON = { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true };

let mine: Array<Record<string, unknown>>;
let org: Array<Record<string, unknown>>;
let live: number;
let refusePin: unknown;

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
  mine = [scheduleView({ agentVersion: 3 })];
  org = [scheduleView({ id: 'sch-9', name: 'Mia nightly', actorId: 'u-mia', input: undefined, agentVersion: 2 })];
  live = 4;
  refusePin = null;
  profile = { isAdmin: false, isInitialized: true };
  useFeatureFlagsStore.setState({ flags: { ENABLE_SCHEDULED_RUNS: true }, agentHarness: HARNESS_ON });
  useSchedulesStore.getState().reset();
  // ADR-UI-03: the list answers each agent's live version; the page reads no versions of its own.
  get.mockImplementation(async (_url: string, config?: { params?: { scope?: string } }) => ({
    data: {
      schedules: (config?.params?.scope === 'org' ? org : mine).map((s) =>
        s.agentKey ? { agentLiveVersion: live, ...s } : s,
      ),
    },
  }));
  put.mockImplementation(async (url: string, body?: unknown) => {
    if (refusePin) throw refusePin;
    const id = url.split('/').slice(-2)[0];
    const { agentVersion } = body as { agentVersion: unknown };
    mine = mine.map((s) => (s.id === id ? { ...s, agentVersion } : s));
    return { data: mine.find((s) => s.id === id) };
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderPage = () => render(<Theme><SchedulesPage /></Theme>);
const versionOf = async (name: string) => within(await screen.findByRole('group', { name })).findByTestId('schedule-version');

describe('WP-21 OWD-14: the agent version a schedule runs', () => {
  it('OWD-14 SV-1: a pinned schedule says which version it runs and that a newer one is live; its owner moves it', async () => {
    renderPage();
    const line = await versionOf('Monday digest');
    expect(within(line).getByText('Runs version 3')).toBeTruthy();
    expect(await within(line).findByText('Version 4 is live')).toBeTruthy();
    expect(get.mock.calls.map(([url]) => url)).toEqual(['/api/v1/agent-harness/schedules']);

    fireEvent.click(within(line).getByRole('button', { name: 'Change version' }));
    const choices = within(line).getByRole('radiogroup');
    expect(within(choices).getAllByRole('radio')).toHaveLength(3);
    expect(within(choices).getByRole('radio', { name: /Keep version 3/ }).getAttribute('aria-checked')).toBe('true');
    expect(within(choices).getByRole('radio', { name: /Move to version 4/ })).toBeTruthy();
    expect(within(choices).getByRole('radio', { name: /Always use the live version/ })).toBeTruthy();
    expect(within(choices).getByText('From the next run. You can move back.')).toBeTruthy();
    const save = within(line).getByRole('button', { name: 'Save' });
    expect(save).toHaveProperty('disabled', true);
    fireEvent.click(within(choices).getByRole('radio', { name: /Move to version 4/ }));
    fireEvent.click(save);

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/v1/agent-harness/schedules/sch-1/agent-version',
        { agentVersion: 4 },
        { suppressErrorToast: true },
      ),
    );
    const moved = await versionOf('Monday digest');
    await waitFor(() => expect(within(moved).getByText('Runs version 4')).toBeTruthy());
    expect(within(moved).queryByText('Version 4 is live')).toBeNull();
    expect(within(moved).queryByRole('radiogroup')).toBeNull();
  });

  it('OWD-14 SV-2: a schedule that follows every publish says so, and can be pinned to the version live now', async () => {
    mine = [scheduleView({ agentVersion: 'latest' })];
    renderPage();
    const line = await versionOf('Monday digest');
    expect(within(line).getByText('Runs the live version')).toBeTruthy();
    fireEvent.click(within(line).getByRole('button', { name: 'Change version' }));
    await waitFor(() => expect(within(line).getByRole('radio', { name: /Keep version 4/ })).toBeTruthy());
    fireEvent.click(within(line).getByRole('radio', { name: /Keep version 4/ }));
    fireEvent.click(within(line).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/v1/agent-harness/schedules/sch-1/agent-version',
        { agentVersion: 4 },
        { suppressErrorToast: true },
      ),
    );
  });

  it('OWD-14 SV-3: a refused move keeps the choices open and says why', async () => {
    refusePin = nodeRefusal(400, 'SCHEDULE_INVALID');
    renderPage();
    const line = await versionOf('Monday digest');
    await within(line).findByText('Version 4 is live');
    fireEvent.click(within(line).getByRole('button', { name: 'Change version' }));
    fireEvent.click(within(line).getByRole('radio', { name: /Always use the live version/ }));
    fireEvent.click(within(line).getByRole('button', { name: 'Save' }));
    expect(await within(line).findByRole('alert')).toBeTruthy();
    expect(within(line).getByRole('radiogroup')).toBeTruthy();
    expect(within(line).getByText('Runs version 3')).toBeTruthy();
  });

  it('ADR-UI-03 SV-4b: a list without the live version (its lookup failed) shows no badge and offers keep or latest', async () => {
    mine = [scheduleView({ agentVersion: 3, agentLiveVersion: null })];
    renderPage();
    const line = await versionOf('Monday digest');
    expect(within(line).getByText('Runs version 3')).toBeTruthy();
    expect(within(line).queryByText(/is live/)).toBeNull();
    fireEvent.click(within(line).getByRole('button', { name: 'Change version' }));
    expect(within(within(line).getByRole('radiogroup')).getAllByRole('radio')).toHaveLength(2);
  });

  it('OWD-14 SV-5: a schedule of the assistant, or from a server without pins, shows no version line', async () => {
    mine = [scheduleView({ agentKey: null, agentVersion: null }), scheduleView({ id: 'sch-2', name: 'Old one' })];
    renderPage();
    for (const name of ['Monday digest', 'Old one']) {
      expect(within(await screen.findByRole('group', { name })).queryByTestId('schedule-version')).toBeNull();
    }
  });
});

describe('WP-21 OWD-14: the pin in the client and the new-schedule form', () => {
  it('ADR-UI-03 SV-6b: reads the agent name and a live version of 1 or more from the list, else null', () => {
    expect(toSchedule(scheduleView({ agentLiveVersion: 4 }))).toMatchObject({ agentName: 'Support agent', agentLiveVersion: 4 });
    for (const bad of [0, '4', 2.5, null, undefined]) {
      expect(toSchedule(scheduleView({ agentLiveVersion: bad }))?.agentLiveVersion).toBeNull();
    }
    expect(toSchedule(scheduleView({ agentName: undefined }))?.agentName).toBeNull();
  });

  it('OWD-14 SV-6: reads a number or `latest`, and nothing else, as the pin', () => {
    expect(toSchedule(scheduleView({ agentVersion: 3 }))?.agentVersion).toBe(3);
    expect(toSchedule(scheduleView({ agentVersion: 'latest' }))?.agentVersion).toBe('latest');
    expect(toSchedule(scheduleView({ agentVersion: 0 }))?.agentVersion).toBeNull();
    expect(toSchedule(scheduleView({ agentVersion: '3' }))?.agentVersion).toBeNull();
    expect(toSchedule(scheduleView())?.agentVersion).toBeNull();
  });

  it('OWD-14 SV-7: a new agent schedule pins the live version unless told to follow every publish', async () => {
    const form = { ...emptyForm(), name: 'Weekly', agent: 'agent-a', query: 'Go', kind: 'webhook' as const, timeZone: 'UTC' };
    expect(toNewSchedule({ ...form, version: 'pinned' })).not.toHaveProperty('agentVersion');
    expect(toNewSchedule({ ...form, version: 'latest' }).agentVersion).toBe('latest');
    expect(toNewSchedule({ ...form, agent: '__assistant__', version: 'latest' })).not.toHaveProperty('agentVersion');

    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { schedule: scheduleView({ agentVersion: 'latest' }) } });
    await SchedulesApi.create(toNewSchedule({ ...form, version: 'latest' }));
    expect(vi.mocked(apiClient.post).mock.calls[0][1]).toMatchObject({ agentKey: 'agent-a', agentVersion: 'latest' });
  });

  it('OWD-14 SV-8: the choices follow the pin and the live version', () => {
    expect(versionChoices(3, 4)).toEqual(['keep', 'move', 'latest']);
    expect(versionChoices(4, 4)).toEqual(['keep', 'latest']);
    expect(versionChoices(3, null)).toEqual(['keep', 'latest']);
    expect(versionChoices('latest', 4)).toEqual(['latest', 'keep']);
    expect(versionChoices('latest', null)).toEqual(['latest']);
    expect(pinOf('move', 3, 4)).toBe(4);
    expect(pinOf('keep', 3, 4)).toBe(3);
    expect(pinOf('keep', 'latest', 4)).toBe(4);
    expect(pinOf('latest', 3, 4)).toBe('latest');
  });
});
