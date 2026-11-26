/**
 * UX-07: "How it runs" on the agent card. The agent's own schedules are shown in words with a way to add one for
 * this agent, and the server's refusal of a schedule is shown in plain words.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { AgentSchedules, SCHEDULES_ROUTE } from '../agent-schedules';
import { AgentRuntimeGroup } from '../agent-runtime-group';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);
const put = vi.mocked(apiClient.put);

const SCHEDULES = '/api/v1/agent-harness/schedules';
const schedule = (over: Record<string, unknown>) => ({
  id: 's-1',
  name: 'Monday digest',
  actorId: 'u-1',
  agentKey: 'a-sales',
  agentVersion: 1,
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'skip' },
  maxCostUsd: null,
  deadlineS: 28800,
  status: 'active',
  statusReason: null,
  nextFireAt: '2026-11-09T08:00:00.000Z',
  createdAt: '2026-11-01T00:00:00.000Z',
  overlap: 'skip',
  notify: ['in_app'],
  ...over,
});

let listed: unknown[];

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  window.history.replaceState(null, '', '/agents/edit?agentKey=a-sales');
  useFeatureFlagsStore.setState({
    flags: { ENABLE_SCHEDULED_RUNS: true },
    agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true },
  });
  listed = [
    schedule({}),
    schedule({ id: 's-2', name: 'Month end report', status: 'paused', statusReason: 'by_actor', nextFireAt: null }),
    schedule({ id: 's-3', name: 'Another agent', agentKey: 'a-other' }),
  ];
  get.mockReset();
  post.mockReset();
  put.mockReset();
  get.mockImplementation((async (url: string) => {
    if (url === SCHEDULES) return { data: { schedules: listed } };
    if (url === `${SCHEDULES}/notify-channels`) return { data: { channels: [{ channel: 'in_app', available: true, reason: null }] } };
    throw new Error(`unexpected ${url}`);
  }) as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('UX-07: the agent card groups how the agent runs', () => {
  it('UX-07 RT-1: one labelled group, "How it runs", holds the runtime parts', () => {
    render(
      <Theme>
        <AgentRuntimeGroup>
          <span>part</span>
        </AgentRuntimeGroup>
      </Theme>,
    );
    const group = screen.getByRole('group', { name: 'How it runs' });
    expect(group.getAttribute('aria-describedby')).toBeTruthy();
    expect(within(group).getByText('part')).toBeTruthy();
    expect(screen.getByText('Model, coding engine, spending, websites and schedules. Each is checked when it is saved.')).toBeTruthy();
  });

  it("UX-07 RT-3: lists only this agent's own schedules, in words: never the raw cron", async () => {
    render(<Theme><AgentSchedules /></Theme>);
    const list = await screen.findByRole('list', { name: 'Schedules' });
    expect(screen.queryByTestId('agent-schedules-paused')).toBeNull();
    const items = within(list).getAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringMatching(/^Monday digestNext run .+Active$/),
      'Month end reportPaused. Resume it under Manage all schedules.Paused',
    ]);
    expect(screen.queryByText(/0 9 \* \* 1/)).toBeNull();
    expect(screen.queryByText('Another agent')).toBeNull();
    expect(get.mock.calls[0]).toEqual([SCHEDULES, expect.objectContaining({ params: { scope: 'mine' } })]);
  });

  it('UX-07 RT-4: "Manage all schedules" goes to the Schedules page, which exists', async () => {
    render(<Theme><AgentSchedules /></Theme>);
    const link = await screen.findByRole('link', { name: 'Manage all schedules' });
    expect(link.getAttribute('href')).toBe(SCHEDULES_ROUTE);
    expect(SCHEDULES_ROUTE).toBe('/workspace/schedules');
  });

  it('UX-07 RT-5: "Add a schedule" opens the form with this agent chosen; the save carries it', async () => {
    post.mockImplementation((async () => ({ data: { schedule: schedule({ id: 's-9', name: 'Daily check' }) } })) as never);
    render(<Theme><AgentSchedules /></Theme>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a schedule' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('combobox', { name: 'Agent' }).textContent).toBe('This agent');
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Daily check' } });
    fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Check the pipeline' } });
    fireEvent.change(within(dialog).getByLabelText('Cron expression'), { target: { value: '0 8 * * *' } });
    listed = [...listed, schedule({ id: 's-9', name: 'Daily check' })];
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![0]).toBe(SCHEDULES);
    expect(post.mock.calls[0]![1]).toMatchObject({ agentKey: 'a-sales', name: 'Daily check' });
    expect(await screen.findByText('Daily check')).toBeTruthy();
  });

  it("UX-07 RT-6: the server's refusal of a budget per run above its ceiling is shown in plain words", async () => {
    post.mockImplementation((async () => {
      throw {
        statusCode: 400,
        message: "A run's budget can be at most USD 40, the ceiling set for this agent",
        originalError: {
          response: {
            data: {
              error: {
                code: 'HTTP_AGENT_HARNESS_SCHEDULE_BUDGET_EXCEEDS_CEILING',
                message: "A run's budget can be at most USD 40, the ceiling set for this agent",
              },
            },
          },
        },
      };
    }) as never);
    render(<Theme><AgentSchedules /></Theme>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a schedule' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Daily check' } });
    fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Check the pipeline' } });
    fireEvent.change(within(dialog).getByLabelText('Cron expression'), { target: { value: '0 8 * * *' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await within(dialog).findByText(
      "The budget per run is too high. A run's budget can be at most USD 40, the ceiling set for this agent",
    )).toBeTruthy();
  });

  it('UX-07 RT-7: a new agent is asked to save first; a viewer cannot add; off without scheduled runs', async () => {
    window.history.replaceState(null, '', '/agents/new');
    const { unmount } = render(<Theme><AgentSchedules /></Theme>);
    expect(screen.getByText('Save the agent to run it on a schedule.')).toBeTruthy();
    expect(get).not.toHaveBeenCalled();
    unmount();

    window.history.replaceState(null, '', '/agents/edit?agentKey=a-sales');
    const viewer = render(<Theme><AgentSchedules readOnly /></Theme>);
    await screen.findByRole('list', { name: 'Schedules' });
    expect(screen.queryByRole('button', { name: 'Add a schedule' })).toBeNull();
    viewer.unmount();

    useFeatureFlagsStore.setState({ flags: {} });
    const off = render(<Theme><AgentSchedules /></Theme>);
    expect(off.container.textContent).toBe('');
  });

  it('UX-07 RT-8: when schedules cannot be read the part says so and offers to try again', async () => {
    get.mockImplementation((async () => {
      throw { statusCode: 503 };
    }) as never);
    render(<Theme><AgentSchedules /></Theme>);
    expect((await screen.findByRole('alert')).textContent).toContain('Schedules cannot be read right now.');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('kill switch + Labs on: the part stays, lists the schedules and says they are paused (ADR-OPS-08)', async () => {
    useFeatureFlagsStore.setState({
      flags: { ENABLE_SCHEDULED_RUNS: true },
      agentHarness: { useHarness: false, harnessMajor: 2, reason: 'kill_switch', resumeInFlightRuns: true, harnessOn: true },
    });
    render(<Theme><AgentSchedules /></Theme>);
    expect(await screen.findByRole('list', { name: 'Schedules' })).toBeTruthy();
    expect(screen.getByTestId('agent-schedules-paused').textContent).toBe(
      "Paused for now: schedules won't run until the agent harness is back.",
    );
  });
});
