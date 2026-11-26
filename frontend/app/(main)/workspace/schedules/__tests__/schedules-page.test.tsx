/**
 * U4 SP-1..SP-11: the schedules page. Behind Labs `ENABLE_SCHEDULED_RUNS`; a
 * webhook secret is shown once; admins see the org's schedules without their
 * prompts and can never run one now.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { nodeRefusal } from '../../harness-ui/__tests__/fake-refusal';
import { ApprovalsDirectory } from '../../approvals/directory';
import { SchedulesPage } from '../components/schedules-page';
import { useSchedulesStore } from '../store';
import { firingView, scheduleView } from './fixtures';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('../../approvals/directory', () => ({
  ApprovalsDirectory: {
    names: vi.fn(async () => ({ 'agent-a': 'Support agent', 'u-ana': 'Ana Admin', 'u-mia': 'Mia Member' })),
    searchAgents: vi.fn(async () => [{ id: 'agent-a', name: 'Support agent' }]),
  },
}));

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace }) }));
// The shared page as it is with no tab beside a person's own schedules, whatever a build adds there.
vi.mock('@/config', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/config')>()), SchedulesOrgTab: null }));

let profile = { isAdmin: false as boolean | null, isInitialized: true };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof profile) => unknown) => selector(profile),
  selectIsAdmin: (s: typeof profile) => s.isAdmin,
  selectIsProfileInitialized: (s: typeof profile) => s.isInitialized,
}));

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);
const del = vi.mocked(apiClient.delete);

let mine: Array<Record<string, unknown>>;
let org: Array<Record<string, unknown>>;
let firings: Array<Record<string, unknown>>;
let fail: Record<string, unknown>;
/** The notice channels Node offers, by agent (absent: the assistant). */
let channels: (agentKey: string | undefined) => Array<Record<string, unknown>>;

const offered = (over: Record<string, Record<string, unknown>> = {}) =>
  ['in_app', 'email', 'slack'].map((channel) => ({ channel, available: true, reason: null, ...(over[channel] ?? {}) }));
const NO_BOT = { available: false, reason: 'no_slack_bot' };

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
  mine = [scheduleView()];
  org = [scheduleView({ id: 'sch-9', name: 'Mia nightly', actorId: 'u-mia', input: undefined })];
  firings = [
    firingView(),
    firingView({ id: 'f2', status: 'refused', reason: 'agent_forbidden', runId: null, conversationId: null }),
    firingView({ id: 'f3', status: 'skipped', reason: 'catch_up_skip', missed: 3, runId: null, conversationId: null }),
    firingView({ id: 'f4', status: 'skipped', reason: 'overlap_skip', runId: null, conversationId: null }),
    firingView({ id: 'f5', status: 'skipped', reason: 'overlap_queue_full', runId: null, conversationId: null }),
    firingView({ id: 'f6', status: 'pending', reason: 'overlap_queued', runId: null, conversationId: null }),
  ];
  fail = {};
  channels = (agentKey) => offered(agentKey === 'agent-a' ? {} : { slack: NO_BOT });
  profile = { isAdmin: false, isInitialized: true };
  useFeatureFlagsStore.setState({ flags: { ENABLE_SCHEDULED_RUNS: true }, agentHarness: HARNESS_ON });
  useSchedulesStore.getState().reset();
  get.mockImplementation(async (url: string, config?: { params?: { scope?: string; agentKey?: string } }) => {
    const key = `GET ${url}${config?.params?.scope ? `?${config.params.scope}` : ''}`;
    if (fail[key]) throw fail[key];
    if (url.endsWith('/firings')) return { data: { firings } };
    if (url.endsWith('/notify-channels')) return { data: { channels: channels(config?.params?.agentKey) } };
    return { data: { schedules: config?.params?.scope === 'org' ? org : mine } };
  });
  post.mockImplementation(async (url: string, body?: unknown) => {
    const key = `POST ${url}`;
    if (fail[key]) throw fail[key];
    if (url === '/api/v1/agent-harness/schedules') {
      const input = body as { name: string; trigger: { kind: string; auth?: string } };
      const webhook = input.trigger.kind === 'webhook';
      const created = scheduleView({
        id: 'sch-new',
        name: input.name,
        trigger: webhook ? { kind: 'webhook', coalesceS: 300, auth: input.trigger.auth } : (input.trigger as unknown),
      });
      mine = [...mine, created];
      const secret = !webhook ? {} : input.trigger.auth === 'token' ? { webhookToken: 'whsec-SHOWN-ONCE' } : { webhookSecret: 'pxs_SIGNING-KEY' };
      return { data: { schedule: created, ...secret } };
    }
    if (url.endsWith('/run')) return { data: firingView({ id: 'f-now', kind: 'manual', conversationId: 'conv-now' }) };
    const id = url.split('/').slice(-2)[0];
    const status = url.endsWith('/pause') ? 'paused' : 'active';
    mine = mine.map((s) => (s.id === id ? { ...s, status, statusReason: status === 'paused' ? 'by_actor' : null } : s));
    return { data: mine.find((s) => s.id === id) ?? scheduleView({ id, status }) };
  });
  del.mockImplementation(async (url: string) => {
    if (fail[`DELETE ${url}`]) throw fail[`DELETE ${url}`];
    mine = mine.filter((s) => !url.endsWith(`/${String(s.id)}`));
    return { data: undefined };
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderPage = () => render(<Theme><SchedulesPage /></Theme>);
const row = async (name: string) => screen.findByRole('group', { name });

const fillCron = (dialog: HTMLElement, { name = 'Weekly', cron = '0 9 * * 1' } = {}) => {
  fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: name } });
  fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Summarise' } });
  fireEvent.change(within(dialog).getByLabelText('Cron expression'), { target: { value: cron } });
  fireEvent.change(within(dialog).getByLabelText('Time zone'), { target: { value: 'Europe/Berlin' } });
};

const HARNESS_ON = { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true };

describe('SchedulesPage', () => {
  it('SP-1: with the Labs flag off the page sends the reader to General and requests nothing', async () => {
    useFeatureFlagsStore.setState({ flags: {}, agentHarness: null });
    renderPage();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/workspace/general'));
    expect(get).not.toHaveBeenCalled();
    expect(screen.queryByText('Schedules')).toBeNull();
  });

  it('UX3 SE-1: the scheduled-runs flag on but the harness off for the org: nothing that could never run is offered', async () => {
    useFeatureFlagsStore.setState({
      flags: { ENABLE_SCHEDULED_RUNS: true },
      agentHarness: { ...HARNESS_ON, useHarness: false, reason: 'org_disabled', resumeInFlightRuns: false, harnessOn: false },
    });
    renderPage();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/workspace/general'));
    expect(get).not.toHaveBeenCalled();
  });

  it('kill switch + Labs on: the page stays (ADR-OPS-08) and says schedules are paused; admission refuses the firings', async () => {
    useFeatureFlagsStore.setState({
      flags: { ENABLE_SCHEDULED_RUNS: true },
      agentHarness: { ...HARNESS_ON, useHarness: false, reason: 'kill_switch' },
    });
    renderPage();
    expect(await screen.findByText('Monday digest')).toBeTruthy();
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByTestId('schedules-paused').textContent).toBe(
      "Paused for now: schedules won't run until the agent harness is back.",
    );
  });

  it('says nothing is paused while new runs are admitted', async () => {
    renderPage();
    expect(await screen.findByText('Monday digest')).toBeTruthy();
    expect(screen.queryByTestId('schedules-paused')).toBeNull();
  });

  it('SP-2: loading, then an outage says "try again", then the empty state', async () => {
    fail['GET /api/v1/agent-harness/schedules?mine'] = nodeRefusal(503, null);
    renderPage();
    expect(screen.getByRole('status').textContent).toMatch(/Loading/);
    expect(await screen.findByText(/Schedules can't be reached right now/)).toBeTruthy();
    delete fail['GET /api/v1/agent-harness/schedules?mine'];
    mine = [];
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('No schedules yet.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'New schedule' })).toBeTruthy();
    // BETA: the page's title carries the Beta badge.
    expect(screen.getByText(i18n.t('common.beta.label'))).toBeTruthy();
  });

  it('ADR-UI-03 SP-3b: rows name their agents from the list itself; only the org view names owners, in one request', async () => {
    const directory = vi.mocked(ApprovalsDirectory.names);
    mine = Array.from({ length: 30 }, (_, i) =>
      scheduleView({ id: `sch-${String(i)}`, name: `Digest ${String(i)}`, agentKey: `agent-${String(i)}`, agentName: `Agent ${String(i)}` }),
    );
    mine.push(scheduleView({ id: 'sch-x', name: 'Unnamed', agentKey: 'agent-x', agentName: null }));
    renderPage();
    expect(within(await row('Digest 29')).getByText(/Agent 29/)).toBeTruthy();
    expect(within(await row('Unnamed')).getByText(/agent-x/)).toBeTruthy();
    expect(directory).not.toHaveBeenCalled();
    expect(get.mock.calls.filter(([url]) => String(url).startsWith('/api/v1/agents'))).toEqual([]);
  });

  it('SP-3: a row says what runs, when, its budget, and why the system paused it', async () => {
    mine = [scheduleView(), scheduleView({ id: 'sch-2', name: 'Paused one', status: 'paused', statusReason: 'actor_disabled', nextFireAt: null, maxCostUsd: null })];
    renderPage();
    const first = await row('Monday digest');
    expect(within(first).getByText(/Support agent/)).toBeTruthy();
    expect(within(first).getByText('0 9 * * 1')).toBeTruthy();
    expect(within(first).getByText(/Europe\/Berlin/)).toBeTruthy();
    expect(within(first).getByText(/Up to 2 USD per run/)).toBeTruthy();
    expect(within(first).getByText('Summarise new tickets')).toBeTruthy();
    const paused = await row('Paused one');
    expect(within(paused).getByRole('status').textContent).toBe("Paused by PipesHub: its owner's account is disabled");
    expect(within(paused).getByText(/No run planned/)).toBeTruthy();
    expect(within(paused).getByRole('button', { name: 'Resume' })).toBeTruthy();
  });

  it('SP-4: creating a cron schedule sends every field and lists it', async () => {
    renderPage();
    await row('Monday digest');
    fireEvent.click(screen.getByRole('button', { name: 'New schedule' }));
    const dialog = await screen.findByRole('dialog');
    fillCron(dialog);
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Run once for the latest missed time' }));
    fireEvent.change(within(dialog).getByLabelText('Budget per run, USD (optional)'), { target: { value: '1.5' } });
    fireEvent.change(within(dialog).getByLabelText('Deadline, hours (optional)'), { target: { value: '2' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(post).toHaveBeenCalledWith(
      '/api/v1/agent-harness/schedules',
      {
        name: 'Weekly',
        input: { query: 'Summarise' },
        trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'run_latest' },
        maxCostUsd: 1.5,
        deadlineS: 7200,
        overlap: 'skip',
        notify: ['in_app'],
      },
      expect.objectContaining({ suppressErrorToast: true }),
    );
    expect(await row('Weekly')).toBeTruthy();
  });

  it('SP-5: a token webhook secret is shown once and gone when the dialog closes', async () => {
    renderPage();
    await row('Monday digest');
    fireEvent.click(screen.getByRole('button', { name: 'New schedule' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Hook' } });
    fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Go' } });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'When a webhook is called' }));
    // TR-04 fallback: the caller can't sign, so it gets a token for a header.
    fireEvent.click(within(dialog).getByRole('checkbox', { name: /The caller can't sign/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    const secretDialog = await screen.findByRole('dialog', { name: 'Your webhook' });
    expect((within(secretDialog).getByLabelText('Secret') as HTMLInputElement).value).toBe('whsec-SHOWN-ONCE');
    expect((within(secretDialog).getByLabelText('Address') as HTMLInputElement).value).toMatch(
      /\/api\/v1\/agent-harness\/triggers\/sch-new\/webhook$/,
    );
    expect(within(secretDialog).getByText(/shown only this once/)).toBeTruthy();
    expect(within(secretDialog).getByRole('button', { name: 'Copy secret' })).toBeTruthy();
    expect(within(secretDialog).getByText('X-PipesHub-Trigger-Token')).toBeTruthy();
    expect(within(secretDialog).queryByRole('region', { name: 'Signing example' })).toBeNull();
    expect((post.mock.calls.find((c) => c[0] === '/api/v1/agent-harness/schedules')?.[1] as { trigger: unknown }).trigger).toEqual({
      kind: 'webhook',
      auth: 'token',
    });
    fireEvent.keyDown(secretDialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.body.innerHTML).not.toContain('whsec-SHOWN-ONCE');
    expect(useSchedulesStore.getState().mine.status === 'ready' && JSON.stringify(useSchedulesStore.getState().mine)).not.toContain('whsec');
  });

  it.each([
    ['SCHEDULE_INVALID', 400, 'trigger.cron fires more often than every 5 minutes', /Check the schedule\. trigger\.cron fires more often/],
    // UX-07: the limit is the lower of 5 USD and a ceiling an extension sets, so the server's words name it.
    ['SCHEDULE_BUDGET_EXCEEDS_CEILING', 400, "A run's budget can be at most USD 2, the ceiling set for this agent", /^The budget per run is too high\. A run's budget can be at most USD 2, the ceiling set for this agent$/],
    ['SCHEDULE_LIMIT', 409, 'x', /reached the number of schedules allowed/],
    ['SCHEDULE_FORBIDDEN', 403, 'x', /can't do this with this schedule/],
    ['SCHEDULED_RUNS_DISABLED', 404, 'x', /Scheduled runs are turned off for your organization/],
  ])('SP-6: %s on create is shown in the dialog, which stays open', async (code, status, message, shown) => {
    fail['POST /api/v1/agent-harness/schedules'] = nodeRefusal(status, code, message);
    renderPage();
    await row('Monday digest');
    fireEvent.click(screen.getByRole('button', { name: 'New schedule' }));
    const dialog = await screen.findByRole('dialog');
    fillCron(dialog, { cron: '* * * * *' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect((await within(dialog).findByRole('alert')).textContent).toMatch(shown);
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('SP-7: pause and resume by status; delete asks first; refusals are shown', async () => {
    renderPage();
    let first = await row('Monday digest');
    fireEvent.click(within(first).getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(within(first).getByRole('button', { name: 'Resume' })).toBeTruthy());
    expect(post).toHaveBeenCalledWith('/api/v1/agent-harness/schedules/sch-1/pause', undefined, { suppressErrorToast: true });

    fail['POST /api/v1/agent-harness/schedules/sch-1/resume'] = nodeRefusal(409, 'SCHEDULE_DISABLED');
    fireEvent.click(within(first).getByRole('button', { name: 'Resume' }));
    expect((await within(first).findByRole('alert')).textContent).toMatch(/turned off because its owner can no longer run it/);

    fireEvent.click(within(first).getByRole('button', { name: 'Delete' }));
    let confirm = await screen.findByRole('alertdialog');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
    expect(del).not.toHaveBeenCalled();

    fail['DELETE /api/v1/agent-harness/schedules/sch-1'] = nodeRefusal(404, 'SCHEDULE_NOT_FOUND');
    first = await row('Monday digest');
    fireEvent.click(within(first).getByRole('button', { name: 'Delete' }));
    confirm = await screen.findByRole('alertdialog');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    expect((await within(first).findByRole('alert')).textContent).toMatch(/no longer exists/);

    delete fail['DELETE /api/v1/agent-harness/schedules/sch-1'];
    fireEvent.click(within(first).getByRole('button', { name: 'Delete' }));
    confirm = await screen.findByRole('alertdialog');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Monday digest' })).toBeNull());
  });

  it('SP-8: run now uses a fresh key per click and links the started conversation', async () => {
    renderPage();
    const first = await row('Monday digest');
    fireEvent.click(within(first).getByRole('button', { name: 'Run now' }));
    const link = await within(first).findByRole('link', { name: 'Open the conversation' });
    expect(link.getAttribute('href')).toBe('/chat/?agentId=agent-a&conversationId=conv-now');
    fireEvent.click(within(first).getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(post.mock.calls.filter((c) => String(c[0]).endsWith('/run'))).toHaveLength(2));
    const keys = post.mock.calls
      .filter((c) => String(c[0]).endsWith('/run'))
      .map((c) => (c[2] as { headers: Record<string, string> }).headers['Idempotency-Key']);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it('SP-9: the history shows admitted, refused and skipped firings in words', async () => {
    renderPage();
    const first = await row('Monday digest');
    fireEvent.click(within(first).getByRole('button', { name: 'History' }));
    const list = await within(first).findByRole('list', { name: 'Runs of Monday digest' });
    const items = within(list).getAllByRole('listitem');
    expect(within(items[0]).getByRole('link', { name: 'Open the conversation' }).getAttribute('href')).toBe(
      '/chat/?agentId=agent-a&conversationId=conv-1',
    );
    expect(items[1].textContent).toMatch(/Not started: its owner can no longer use this agent/);
    expect(items[2].textContent).toMatch(/Missed while PipesHub was down: 3/);
    // TR-06: a slot the overlap policy passed over or held says so, never "missed while down".
    expect(items[3].textContent).toMatch(/Skipped: the last run was still going/);
    expect(items[4].textContent).toMatch(/Skipped: a run was already waiting/);
    expect(items[5].textContent).toMatch(/Waiting for the last run to finish/);
  });

  it('SP-10: an admin sees their own schedules only: no organization tab, nothing asked beyond their own', async () => {
    profile = { isAdmin: true, isInitialized: true };
    renderPage();
    await row('Monday digest');
    expect(screen.queryByRole('tab', { name: /Organization/ })).toBeNull();
    expect(screen.queryByText('Mia nightly')).toBeNull();
    expect(get.mock.calls.every(([, config]) => (config as { params?: { scope?: string } } | undefined)?.params?.scope !== 'org')).toBe(true);
  });

  it('SP-11: the create dialog is labelled and closes on Escape without sending', async () => {
    renderPage();
    await row('Monday digest');
    fireEvent.click(screen.getByRole('button', { name: 'New schedule' }));
    const dialog = await screen.findByRole('dialog', { name: 'New schedule' });
    expect(within(dialog).getByRole('radiogroup', { name: 'When it runs' })).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Create' }).hasAttribute('disabled')).toBe(true);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(post).not.toHaveBeenCalled();
  });

  describe('WP-24 trigger settings (TR-02, TR-04, TR-06, TR-07)', () => {
    const openForm = async () => {
      renderPage();
      await row('Monday digest');
      fireEvent.click(screen.getByRole('button', { name: 'New schedule' }));
      return screen.findByRole('dialog', { name: 'New schedule' });
    };
    const checkbox = (dialog: HTMLElement, name: string) => within(dialog).getByRole('checkbox', { name }) as HTMLButtonElement;
    const pickAgent = async (dialog: HTMLElement, name: string) => {
      fireEvent.click(within(dialog).getByRole('combobox', { name: 'Agent' }));
      fireEvent.click(await screen.findByRole('option', { name }));
    };
    const sent = () =>
      post.mock.calls.find((c) => c[0] === '/api/v1/agent-harness/schedules')?.[1] as Record<string, unknown> | undefined;

    it('TR-UI-1: the form defaults to skipping missed and overlapping runs and telling in PipesHub; Slack is greyed for the assistant', async () => {
      const dialog = await openForm();
      const catchUp = within(dialog).getByRole('radiogroup', { name: 'If PipesHub was down' });
      expect((within(catchUp).getByRole('radio', { name: /Skip the runs it missed/ }) as HTMLButtonElement).getAttribute('aria-checked')).toBe('true');
      expect(within(catchUp).getAllByRole('radio')).toHaveLength(3);
      expect(within(catchUp).getByRole('radio', { name: /Run once for each missed time/ }).getAttribute('aria-describedby')).toBeTruthy();
      expect(within(catchUp).getByText('Up to 24, oldest first, one after another.')).toBeTruthy();
      const overlap = within(dialog).getByRole('radiogroup', { name: 'If the last run is still going' });
      expect(within(overlap).getByRole('radio', { name: /Skip this run/ }).getAttribute('aria-checked')).toBe('true');
      expect(within(overlap).getByText(/One run can wait/)).toBeTruthy();
      const notify = within(dialog).getByRole('group', { name: 'Tell me when a run ends' });
      await waitFor(() => expect(checkbox(notify, 'Email').disabled).toBe(false));
      expect(checkbox(notify, 'In PipesHub').getAttribute('aria-checked')).toBe('true');
      expect(checkbox(notify, 'Email').getAttribute('aria-checked')).toBe('false');
      expect(checkbox(notify, 'Slack').disabled).toBe(true);
      expect(within(notify).getByText(/The assistant has no Slack bot/)).toBeTruthy();
      expect(get).toHaveBeenCalledWith('/api/v1/agent-harness/schedules/notify-channels', expect.objectContaining({ params: {} }));
    });

    it('TR-UI-2: run all, wait for the last run, and email plus Slack from an agent with a bot are sent as chosen', async () => {
      const dialog = await openForm();
      fillCron(dialog);
      await pickAgent(dialog, 'Support agent');
      fireEvent.click(within(dialog).getByRole('radio', { name: /Run once for each missed time/ }));
      fireEvent.click(within(dialog).getByRole('radio', { name: /Wait for it to finish, then run/ }));
      const notify = within(dialog).getByRole('group', { name: 'Tell me when a run ends' });
      await waitFor(() => expect(checkbox(notify, 'Slack').disabled).toBe(false));
      fireEvent.click(checkbox(notify, 'Email'));
      fireEvent.click(checkbox(notify, 'Slack'));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      await waitFor(() => expect(sent()).toBeTruthy());
      expect(sent()).toMatchObject({
        agentKey: 'agent-a',
        trigger: { kind: 'cron', catchUp: 'run_all' },
        overlap: 'queue',
        notify: ['in_app', 'email', 'slack'],
      });
    });

    it('TR-UI-3: "run both" is sent; a channel the org turned off and an agent without a bot are greyed with the reason', async () => {
      channels = () => offered({ email: { available: false, reason: 'org_off' }, slack: NO_BOT });
      const dialog = await openForm();
      fillCron(dialog);
      await pickAgent(dialog, 'Support agent');
      const notify = within(dialog).getByRole('group', { name: 'Tell me when a run ends' });
      await waitFor(() => expect(within(notify).getByText(/This agent has no Slack bot\. Add one in Slack settings/)).toBeTruthy());
      expect(checkbox(notify, 'Email').disabled).toBe(true);
      expect(within(notify).getByText('Your organization turned this off.')).toBeTruthy();
      fireEvent.click(within(dialog).getByRole('radio', { name: /Run both at the same time/ }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      await waitFor(() => expect(sent()).toMatchObject({ overlap: 'allow', notify: ['in_app'] }));
    });

    it('TR-UI-4: when the channels cannot be checked only PipesHub is offered, and the form still creates', async () => {
      fail['GET /api/v1/agent-harness/schedules/notify-channels'] = nodeRefusal(503, null);
      const dialog = await openForm();
      const notify = within(dialog).getByRole('group', { name: 'Tell me when a run ends' });
      expect(await within(notify).findByText(/Couldn't check which channels you can use/)).toBeTruthy();
      expect(checkbox(notify, 'In PipesHub').disabled).toBe(false);
      expect(checkbox(notify, 'Email').disabled).toBe(true);
      expect(checkbox(notify, 'Slack').disabled).toBe(true);
      fillCron(dialog);
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      await waitFor(() => expect(sent()).toMatchObject({ notify: ['in_app'] }));
    });

    it('TR-UI-5: a webhook is signed by default; its key is shown once with how to sign, and gone after', async () => {
      const dialog = await openForm();
      fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Hook' } });
      fireEvent.change(within(dialog).getByLabelText('What to ask'), { target: { value: 'Go' } });
      fireEvent.click(within(dialog).getByRole('radio', { name: 'When a webhook is called' }));
      expect(checkbox(dialog, "The caller can't sign. Use a token in a header instead").getAttribute('aria-checked')).toBe('false');
      expect(within(dialog).getByText('Anyone who sees the token can call it. Signing is safer.')).toBeTruthy();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      const secretDialog = await screen.findByRole('dialog', { name: 'Your webhook' });
      expect(sent()?.trigger).toEqual({ kind: 'webhook', auth: 'signature' });
      expect((within(secretDialog).getByLabelText('Secret') as HTMLInputElement).value).toBe('pxs_SIGNING-KEY');
      expect(within(secretDialog).getByText("Copy this secret now. You won't see it again.")).toBeTruthy();
      expect(within(secretDialog).getByText(/Sign each call/)).toBeTruthy();
      const example = within(secretDialog).getByRole('region', { name: 'Signing example' }).textContent ?? '';
      expect(example).toContain('X-PipesHub-Timestamp: $ts');
      expect(example).toContain('X-PipesHub-Signature: v1=$sig');
      expect(example).toContain('openssl dgst -sha256 -hmac "$SECRET"');
      expect(example).toMatch(/\/api\/v1\/agent-harness\/triggers\/sch-new\/webhook/);
      // The key is never written into the example: it is copied separately.
      expect(example).not.toContain('pxs_SIGNING-KEY');
      expect(within(secretDialog).queryByText('X-PipesHub-Trigger-Token')).toBeNull();
      expect(within(secretDialog).getByRole('button', { name: 'Copy example' })).toBeTruthy();
      fireEvent.keyDown(secretDialog, { key: 'Escape' });
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(document.body.innerHTML).not.toContain('pxs_SIGNING-KEY');
    });

    it('TR-UI-6: the history marks a slot run after an outage as caught up; a run all row says so in the summary', async () => {
      mine = [scheduleView({ trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'run_all' }, overlap: 'queue' })];
      firings = [
        firingView({ id: 'late', slotAt: '2026-10-04T07:00:00.000Z', createdAt: '2026-10-05T06:00:00.000Z', missed: 0 }),
        firingView({ id: 'onTime', slotAt: '2026-10-05T07:00:00.000Z', createdAt: '2026-10-05T07:00:01.000Z' }),
        firingView({ id: 'manual', kind: 'manual', slotAt: '2026-10-05T07:00:00.000Z', createdAt: '2026-10-05T09:00:00.000Z' }),
      ];
      renderPage();
      const first = await row('Monday digest');
      expect(within(first).getByText(/Run once for each missed time/)).toBeTruthy();
      fireEvent.click(within(first).getByRole('button', { name: 'History' }));
      const items = within(await within(first).findByRole('list', { name: 'Runs of Monday digest' })).getAllByRole('listitem');
      expect(items[0].textContent).toContain('Scheduled (caught up)');
      expect(items[1].textContent).not.toContain('caught up');
      expect(items[2].textContent).not.toContain('caught up');
    });
  });
});
