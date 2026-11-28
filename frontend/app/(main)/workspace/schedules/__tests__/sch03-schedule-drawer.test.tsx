/**
 * The schedule drawer (ADR-SCH-03 D4): New schedule and Edit open the same workspace side drawer. Focus moves into
 * it and back to the button that opened it; Esc closes it; unsaved changes ask first; on a phone it is a full-width
 * sheet. An edit is prefilled, sends only what changed with the schedule's revision, and a stale revision says so
 * and shows the schedule as it is now.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { nodeRefusal } from '../../harness-ui/__tests__/fake-refusal';
import { SchedulesPage } from '../components/schedules-page';
import { useSchedulesStore } from '../store';
import { scheduleView } from './fixtures';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));
vi.mock('../../approvals/directory', () => ({
  ApprovalsDirectory: {
    names: vi.fn(async () => ({})),
    searchAgents: vi.fn(async () => [{ id: 'agent-a', name: 'Support agent' }]),
  },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock('@/config', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/config')>()), SchedulesOrgTab: null }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: object) => unknown) => selector({ isAdmin: false, isInitialized: true }),
  selectIsAdmin: () => false,
  selectIsProfileInitialized: () => true,
}));
const mobile = vi.hoisted(() => ({ on: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.on }));

const get = vi.mocked(apiClient.get);
const patch = vi.mocked(apiClient.patch);

const HARNESS_ON = { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true };
let mine: Array<Record<string, unknown>>;

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
  mobile.on = false;
  mine = [scheduleView({ revision: 4, notify: ['in_app'], overlap: 'skip' })];
  useFeatureFlagsStore.setState({ flags: { ENABLE_SCHEDULED_RUNS: true }, agentHarness: HARNESS_ON });
  useSchedulesStore.getState().reset();
  get.mockImplementation(async (url: string) => {
    if (url.endsWith('/notify-channels')) return { data: { channels: [{ channel: 'in_app', available: true, reason: null }, { channel: 'email', available: true, reason: null }] } };
    if (url.endsWith('/firings')) return { data: { firings: [] } };
    return { data: { schedules: mine } };
  });
  patch.mockImplementation(async (_url: string, body?: unknown) => {
    const changes = body as { revision: number; input?: { query: string }; name?: string };
    mine = mine.map((s) => ({
      ...s,
      revision: changes.revision + 1,
      ...(changes.name ? { name: changes.name } : {}),
      ...(changes.input ? { input: changes.input } : {}),
    }));
    return { data: mine[0] };
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderPage = () => render(<Theme><SchedulesPage /></Theme>);
const drawer = (name: string) => screen.findByRole('dialog', { name });
const openNew = async () => {
  renderPage();
  await screen.findByText('Monday digest');
  const button = screen.getByRole('button', { name: 'New schedule' });
  button.focus();
  fireEvent.click(button);
  return { button, panel: await drawer('New schedule') };
};
const openEdit = async () => {
  renderPage();
  await screen.findByText('Monday digest');
  const button = screen.getByRole('button', { name: 'Edit Monday digest' });
  button.focus();
  fireEvent.click(button);
  return { button, panel: await drawer('Edit schedule') };
};

describe('New schedule opens the drawer', () => {
  it('as the workspace side drawer, focus inside; Esc closes it and focus goes back to the button', async () => {
    const { button, panel } = await openNew();
    expect(panel.hasAttribute('data-ph-workspace-drawer-panel')).toBe(true);
    expect(panel.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New schedule' })).toBeNull());
    expect(document.activeElement).toBe(button);
  });

  it('Cancel with nothing entered closes it at once', async () => {
    const { panel } = await openNew();
    fireEvent.click(within(panel).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New schedule' })).toBeNull());
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('unsaved changes ask before closing: Keep editing keeps them, Discard closes', async () => {
    const { panel } = await openNew();
    fireEvent.change(within(panel).getByLabelText('Name'), { target: { value: 'Weekly' } });
    fireEvent.keyDown(window, { key: 'Escape' });
    const ask = await screen.findByRole('alertdialog');
    expect(within(ask).getByText('Discard this schedule?')).toBeTruthy();
    fireEvent.click(within(ask).getByRole('button', { name: 'Keep editing' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    expect((within(panel).getByLabelText('Name') as HTMLInputElement).value).toBe('Weekly');

    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New schedule' })).toBeNull());
  });

  it('says a cron in words as it is typed', async () => {
    const { panel } = await openNew();
    fireEvent.change(within(panel).getByLabelText('Cron expression'), { target: { value: '0 9 * * 1-5' } });
    fireEvent.change(within(panel).getByLabelText('Time zone'), { target: { value: 'Europe/Berlin' } });
    expect(screen.getByTestId('schedule-cron-said').textContent?.replace(/[  ]/g, ' ')).toBe('Every weekday at 9:00 AM (Europe/Berlin)');
  });

  it('is a full-width sheet on a phone', async () => {
    mobile.on = true;
    const { panel } = await openNew();
    expect(panel.style.width).toBe('100%');
    expect(panel.style.maxWidth).toBe('none');
  });
});

describe('Edit opens the same drawer, prefilled', () => {
  it('shows the schedule as it is; Save waits for a change; only what changed is sent, with its revision', async () => {
    const { button, panel } = await openEdit();
    expect((within(panel).getByLabelText('Name') as HTMLInputElement).value).toBe('Monday digest');
    expect((within(panel).getByLabelText('What to ask') as HTMLTextAreaElement).value).toBe('Summarise new tickets');
    expect((within(panel).getByLabelText('Cron expression') as HTMLInputElement).value).toBe('0 9 * * 1');
    expect((within(panel).getByLabelText('Time zone') as HTMLInputElement).value).toBe('Europe/Berlin');
    // Set when it was made: not part of an edit.
    expect(within(panel).queryByLabelText('Budget per run, USD (optional)')).toBeNull();
    expect(within(panel).queryByRole('radio', { name: 'When a webhook is called' })).toBeNull();
    expect(within(panel).getByTestId('schedule-edit-fixed')).toBeTruthy();
    const save = () => within(panel).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save().disabled).toBe(true);

    fireEvent.change(within(panel).getByLabelText('What to ask'), { target: { value: 'Summarise new and reopened tickets' } });
    expect(save().disabled).toBe(false);
    fireEvent.click(save());
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Edit schedule' })).toBeNull());
    expect(patch).toHaveBeenCalledWith(
      '/api/v1/agent-harness/schedules/sch-1',
      { revision: 4, input: { query: 'Summarise new and reopened tickets' } },
      expect.objectContaining({ suppressErrorToast: true }),
    );
    expect(document.activeElement).toBe(button);
  });

  it('a new time is sent as the trigger; notices as the full list', async () => {
    const { panel } = await openEdit();
    fireEvent.change(within(panel).getByLabelText('Cron expression'), { target: { value: '30 8 * * 1' } });
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'Email' }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0][1]).toEqual({
      revision: 4,
      trigger: { kind: 'cron', cron: '30 8 * * 1', timeZone: 'Europe/Berlin' },
      notify: ['in_app', 'email'],
    });
  });

  it('a stale revision says the schedule changed, and shows it as it is now to be changed again', async () => {
    patch.mockImplementationOnce(async () => {
      // Someone changed it meanwhile.
      mine = [scheduleView({ revision: 5, input: { query: 'Changed elsewhere' } })];
      throw nodeRefusal(409, 'SCHEDULE_CHANGED');
    });
    const { panel } = await openEdit();
    fireEvent.change(within(panel).getByLabelText('Name'), { target: { value: 'Weekly digest' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    expect((await within(panel).findByRole('alert')).textContent).toBe(
      'This schedule was changed somewhere else while you were editing. It now shows what was saved; make your change again.',
    );
    await waitFor(() =>
      expect((within(panel).getByLabelText('What to ask') as HTMLTextAreaElement).value).toBe('Changed elsewhere'),
    );
    expect((within(panel).getByLabelText('Name') as HTMLInputElement).value).toBe('Monday digest');
    fireEvent.change(within(panel).getByLabelText('Name'), { target: { value: 'Weekly digest' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    expect(patch.mock.calls[1][1]).toEqual({ revision: 5, name: 'Weekly digest' });
  });

  it('unsaved edits ask before closing', async () => {
    const { panel } = await openEdit();
    fireEvent.change(within(panel).getByLabelText('Name'), { target: { value: 'Weekly digest' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Cancel' }));
    expect(within(await screen.findByRole('alertdialog')).getByText('Discard your changes?')).toBeTruthy();
  });

  it('a one-off moved to a time already past is refused at save, against the clock', async () => {
    mine = [scheduleView({ revision: 1, trigger: { kind: 'once', at: new Date(Date.now() + 3_600_000).toISOString(), timeZone: 'UTC' } })];
    const { panel } = await openEdit();
    const past = new Date(Date.now() - 3_600_000);
    const pad = (n: number) => String(n).padStart(2, '0');
    const value = `${past.getFullYear()}-${pad(past.getMonth() + 1)}-${pad(past.getDate())}T${pad(past.getHours())}:${pad(past.getMinutes())}`;
    fireEvent.change(within(panel).getByLabelText('Date and time'), { target: { value } });
    expect(within(panel).getByRole('alert').textContent).toBe('Pick a time at least a minute from now.');
    expect((within(panel).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    expect(patch).not.toHaveBeenCalled();
  });

  it('a schedule that ran once offers no Edit', async () => {
    mine = [scheduleView({ status: 'disabled', statusReason: 'ran_once', nextFireAt: null, trigger: { kind: 'once', at: '2026-01-01T00:00:00.000Z', timeZone: 'UTC' } })];
    renderPage();
    await screen.findByText('Monday digest');
    expect(screen.queryByRole('button', { name: 'Edit Monday digest' })).toBeNull();
  });
});
