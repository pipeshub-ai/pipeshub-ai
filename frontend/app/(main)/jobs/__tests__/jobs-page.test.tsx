import React, { useLayoutEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { SWRConfig, useSWRConfig } from 'swr';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

/**
 * CJ-4 (classic jobs plan §4.3, §4.4, D6, D7, D9, D11): the Jobs page's two tabs from the URL, its sections, Stop,
 * Clear finished with Undo, Load more, Needs you's groups and its links into the chat, and every empty state.
 */

installMemoryStorage();

const nav = vi.hoisted(() => ({ params: new URLSearchParams('view=all'), replace: vi.fn(), push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: nav.replace, push: nav.push, back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => '/jobs/',
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));

const api = vi.hoisted(() => ({ personJobs: vi.fn(), stop: vi.fn() }));
vi.mock('@/lib/harness-jobs/jobs-api', async (load) => {
  const real = await load<typeof import('@/lib/harness-jobs/jobs-api')>();
  return { ...real, JobsApi: { ...real.JobsApi, personJobs: api.personJobs, stop: api.stop } };
});
const inbox = vi.hoisted(() => ({ feed: vi.fn(), count: vi.fn() }));
vi.mock('@/lib/harness-jobs/inbox-api', () => ({ InboxApi: inbox, personTimeZone: () => 'UTC' }));
const toasts = vi.hoisted(() => ({ success: vi.fn(), info: vi.fn(), error: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toasts }));

// The app refreshes through SWR's global cache (cj1-data-layer proves the keys); each test here has its own cache.
const bound = vi.hoisted(() => ({ mutate: null as null | ((matcher: (key: unknown) => boolean) => unknown) }));
vi.mock('@/lib/harness-jobs/swr-keys', async (load) => ({
  ...(await load<typeof import('@/lib/harness-jobs/swr-keys')>()),
  refreshJobLists: () => bound.mutate?.((key) => Array.isArray(key) && key[0] === 'harness-jobs'),
}));
function BindRefresh() {
  const { mutate } = useSWRConfig();
  useLayoutEffect(() => {
    bound.mutate = mutate as never;
  }, [mutate]);
  return null;
}

const { default: JobsPage } = await import('../page');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { EMPTY_COUNTS, useInboxStore } = await import('@/lib/harness-jobs/inbox-store');
const { useJobsStore } = await import('@/lib/harness-jobs/jobs-store');
const { chatAtHref, jobsViewOf } = await import('../url-params');

const NOW = Date.now();
const run = (over: Record<string, unknown> = {}) => ({
  runId: 'job-1',
  sessionId: 'conv-1',
  parentRunId: 'turn-1',
  job: true,
  agentKey: null,
  agentName: null,
  conversationTitle: 'Q3 report',
  engine: 'codex',
  state: 'working',
  status: 'running',
  outcome: null,
  createdAt: NOW - 60_000,
  updatedAt: NOW,
  endedAt: null,
  title: 'Build the quarterly PDF report',
  ...over,
});

const ask = (over: Record<string, unknown> = {}) => ({
  id: 'a:job-1:i1',
  kind: 'approval',
  severity: 'warning',
  waiting: true,
  unread: true,
  archived: false,
  title: 'Approve: Create ticket',
  description: 'A task is waiting',
  at: NOW - 120_000,
  pointer: { type: 'conversation', conversationId: 'conv-1', agentKey: 'agent-7', runId: 'job-1', interactionId: 'i1' },
  link: '/chat/?conversationId=conv-1',
  subjectVersion: 1,
  subject: { action: { name: 'Create ticket', app: 'Jira' } },
  ...over,
});

function renderPage() {
  return renderInTheme(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <BindRefresh />
      <JobsPage />
    </SWRConfig>,
  );
}

const region = (name: string) => screen.getByRole('region', { name });
const rowOf = (runId: string) => document.querySelector<HTMLElement>(`[data-testid="job-row"][data-run-id="${runId}"]`);

beforeEach(() => {
  installBrowserShims();
  mobile.value = false;
  nav.params = new URLSearchParams('view=all');
  nav.replace.mockReset();
  api.personJobs.mockReset();
  api.stop.mockReset();
  inbox.feed.mockReset();
  Object.values(toasts).forEach((fn) => fn.mockReset());
  useFeatureFlagsStore.setState({ flags: {}, agentHarness: { useHarness: true, harnessOn: true } } as never);
  useInboxStore.setState({ counts: EMPTY_COUNTS, countsKnown: true, items: [], open: false, loading: false, failed: false, partial: [], seq: 0 });
  useJobsStore.setState({ cleared: [] });
});

afterEach(() => cleanup());

describe('the Jobs page’s URL (D6)', () => {
  it('opens the tab the URL names, else Needs you while something waits, else Jobs', () => {
    expect(jobsViewOf(new URLSearchParams('view=needs-you'), 0)).toBe('needs-you');
    expect(jobsViewOf(new URLSearchParams('view=all'), 4)).toBe('all');
    expect(jobsViewOf(new URLSearchParams(''), 2)).toBe('needs-you');
    expect(jobsViewOf(new URLSearchParams('view=bogus'), 0)).toBe('all');
  });

  it('links into a chat at its job and its ask, keeping an agent chat under its agent', () => {
    expect(chatAtHref({ conversationId: 'c1', jobRunId: 'j1' })).toBe('/chat/?conversationId=c1&job=j1');
    expect(chatAtHref({ conversationId: 'c1', agentKey: 'a7', jobRunId: 'j1', askId: 'i1' })).toBe('/chat/?agentId=a7&conversationId=c1&job=j1&ask=i1');
    expect(chatAtHref({ conversationId: 'c1', askId: 'i1' })).toBe('/chat/?conversationId=c1&ask=i1');
  });

  it('switches tabs through the URL, as Radix tabs named Needs you and Jobs', async () => {
    api.personJobs.mockResolvedValue({ runs: [], nextCursor: null });
    renderPage();
    expect(screen.getByRole('tab', { name: 'Jobs' }).getAttribute('aria-selected')).toBe('true');
    const needsYou = screen.getByRole('tab', { name: 'Needs you' });
    fireEvent.mouseDown(needsYou, { button: 0 });
    expect(nav.replace).toHaveBeenCalledWith('/jobs/?view=needs-you');
  });
});

describe('the Jobs tab', () => {
  it('lists the person’s jobs under Working and Finished, each opening its chat with its panel', async () => {
    api.personJobs.mockResolvedValue({
      runs: [
        run(),
        run({ runId: 'job-2', state: 'finished', status: 'completed', endedAt: NOW - 1_000, title: 'Summarise the contracts', agentKey: 'agent-7' }),
      ],
      nextCursor: null,
    });
    renderPage();
    await waitFor(() => expect(rowOf('job-1')).not.toBeNull());
    expect(within(region('Working')).getByText('Build the quarterly PDF report')).toBeTruthy();
    expect(rowOf('job-1')?.getAttribute('data-status')).toBe('working');
    expect(within(region('Finished')).getByText('Summarise the contracts')).toBeTruthy();
    const open = within(rowOf('job-2') as HTMLElement).getByRole('link', { name: 'Open job: Summarise the contracts' });
    expect(open.getAttribute('href')).toBe('/chat?agentId=agent-7&conversationId=conv-1&job=job-2');
    expect(within(rowOf('job-2') as HTMLElement).queryByRole('button', { name: /^Stop job: / })).toBeNull();
  });

  it('stops a working job in one click on its own cancel route, then reads the list again', async () => {
    api.personJobs.mockResolvedValue({ runs: [run()], nextCursor: null });
    api.stop.mockResolvedValue(true);
    renderPage();
    const stop = await screen.findByRole('button', { name: 'Stop job: Build the quarterly PDF report' });
    api.personJobs.mockResolvedValue({ runs: [run({ state: 'stopped', status: 'cancelled', endedAt: NOW })], nextCursor: null });
    fireEvent.click(stop);
    expect(api.stop).toHaveBeenCalledWith({ sessionId: 'conv-1' }, 'job-1');
    await waitFor(() => expect(rowOf('job-1')?.getAttribute('data-status')).toBe('stopped'));
    expect(rowOf('job-1')?.textContent).toContain('Stopped by you');
    expect(screen.queryByRole('button', { name: /^Stop job: / })).toBeNull();
  });

  it('says it had already finished when Stop comes too late (409)', async () => {
    api.personJobs.mockResolvedValue({ runs: [run()], nextCursor: null });
    api.stop.mockResolvedValue(false);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /^Stop job: / }));
    await waitFor(() => expect(toasts.info).toHaveBeenCalledWith('It had already finished'));
  });

  it('names an admin’s stop in words, never a code', async () => {
    api.personJobs.mockResolvedValue({
      runs: [run({ state: 'stopped', status: 'cancelled', endedAt: NOW, operatorStop: { scope: 'org', reason: 'maintenance', at: NOW } })],
      nextCursor: null,
    });
    renderPage();
    await waitFor(() => expect(rowOf('job-1')?.textContent).toContain('An admin stopped this: maintenance'));
  });

  it('clears finished jobs on this device, with Undo', async () => {
    api.personJobs.mockResolvedValue({ runs: [run({ state: 'finished', status: 'completed', endedAt: NOW })], nextCursor: null });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Clear finished' }));
    expect(rowOf('job-1')).toBeNull();
    expect(screen.getByText('You cleared every finished job on this device.')).toBeTruthy();
    const [title, options] = toasts.success.mock.calls[0];
    expect(title).toBe('Cleared 1 finished job');
    act(() => options.action.onClick());
    await waitFor(() => expect(rowOf('job-1')).not.toBeNull());
  });

  it('loads older jobs from the cursor, without repeating a row', async () => {
    api.personJobs.mockImplementation(async (o: { cursor?: string }) =>
      o.cursor === 'next-1'
        ? { runs: [run({ runId: 'job-1' }), run({ runId: 'job-old', title: 'Older job' })], nextCursor: null }
        : { runs: [run()], nextCursor: 'next-1' },
    );
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Load more' }));
    await waitFor(() => expect(rowOf('job-old')).not.toBeNull());
    expect(document.querySelectorAll('[data-run-id="job-1"]')).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull();
  });

  it('has an empty state, a nothing-running line, a failed state with retry, and a 44 px row and controls on the phone', async () => {
    api.personJobs.mockResolvedValue({ runs: [], nextCursor: null });
    renderPage();
    expect(await screen.findByText('No jobs yet. When an agent hands work to a background job, it shows here.')).toBeTruthy();
    cleanup();

    api.personJobs.mockResolvedValue({ runs: [run({ state: 'finished', status: 'completed', endedAt: NOW })], nextCursor: null });
    renderPage();
    expect(await screen.findByText('Nothing is running.')).toBeTruthy();
    cleanup();

    api.personJobs.mockRejectedValueOnce(new Error('down'));
    renderPage();
    const retry = await screen.findByRole('button', { name: 'Try again' });
    const reads = api.personJobs.mock.calls.length;
    fireEvent.click(retry);
    await waitFor(() => expect(api.personJobs.mock.calls.length).toBe(reads + 1));
    cleanup();

    mobile.value = true;
    api.personJobs.mockResolvedValue({ runs: [run()], nextCursor: null });
    renderPage();
    const stop = await screen.findByRole('button', { name: /^Stop job: / });
    expect(stop.style.minHeight).toBe('44px');
    expect(within(rowOf('job-1') as HTMLElement).getByRole('link').style.minHeight).toBe('44px');
    expect(rowOf('job-1')?.style.minHeight).toBe('44px');
  });

  it('says so, with a way back, when the harness is off', () => {
    useFeatureFlagsStore.setState({ flags: {}, agentHarness: { useHarness: false, harnessOn: false } } as never);
    renderPage();
    expect(screen.getByText('Background jobs are not turned on for your workspace.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Back to chat' }).getAttribute('href')).toBe('/chat');
  });
});

describe('the Needs you tab (D7, D9)', () => {
  beforeEach(() => {
    nav.params = new URLSearchParams('view=needs-you');
    api.personJobs.mockResolvedValue({ runs: [run()], nextCursor: null });
  });

  it('reads the asks only, groups chats and jobs before reviews, and opens an ask in its chat at its card', async () => {
    inbox.feed.mockResolvedValue({
      items: [
        ask(),
        ask({ id: 'q:turn-9:i9', kind: 'question', title: 'A question for you', pointer: { type: 'conversation', conversationId: 'conv-2', runId: 'turn-9', interactionId: 'i9' }, subject: { question: 'Which quarter?' } }),
        ask({ id: 's:cand-1', kind: 'skill_proposal', title: 'New skill', pointer: { type: 'skill_candidate', candidateId: 'cand-1' }, subject: { skill: 'Invoice checks' } }),
      ],
      counts: { ...EMPTY_COUNTS, waiting: 3, badge: 3 },
    });
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('needs-you-row')).toHaveLength(3));
    expect(inbox.feed).toHaveBeenCalledWith({ filter: 'waiting', view: 'inbox' });
    expect(screen.getByRole('tab', { name: 'Needs you (3 things need you)' }).getAttribute('aria-selected')).toBe('true');

    const chats = region('From your chats and jobs');
    const [jobAsk, chatAsk] = within(chats).getAllByTestId('needs-you-row');
    expect(jobAsk.getAttribute('data-run-id')).toBe('job-1');
    expect(jobAsk.getAttribute('data-interaction-id')).toBe('i1');
    expect(jobAsk.textContent).toContain('Approve: Create ticket in Jira');
    expect(jobAsk.textContent).toContain('Build the quarterly PDF report · Q3 report');
    expect(within(jobAsk).getByRole('link', { name: 'Review in chat' }).getAttribute('href')).toBe(
      '/chat?agentId=agent-7&conversationId=conv-1&job=job-1&ask=i1',
    );
    // A chat's own answer is not a job: the link focuses its ask without opening a job panel.
    expect(within(chatAsk).getByRole('link', { name: 'Answer in chat' }).getAttribute('href')).toBe('/chat?conversationId=conv-2&ask=i9');

    const reviews = region('Reviews');
    expect(within(reviews).getByText('New skill proposed: Invoice checks')).toBeTruthy();
    expect(within(reviews).getByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(within(reviews).getByRole('button', { name: 'Reject' })).toBeTruthy();
  });

  it('drops an answered ask at the push that changes the count, not before', async () => {
    inbox.feed.mockResolvedValue({ items: [ask()], counts: { ...EMPTY_COUNTS, waiting: 1, badge: 1 } });
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('needs-you-row')).toHaveLength(1));
    inbox.feed.mockResolvedValue({ items: [], counts: EMPTY_COUNTS });
    const { takeCounts } = await import('@/lib/harness-jobs/inbox-store');
    act(() => takeCounts(EMPTY_COUNTS));
    expect(await screen.findByText('Nothing needs you right now.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'See your jobs' }).getAttribute('href')).toBe('/jobs?view=all');
  });

  it('says when a source did not answer, and offers a retry when the read failed', async () => {
    inbox.feed.mockResolvedValue({ items: [ask()], counts: EMPTY_COUNTS, partial: ['skills'] });
    renderPage();
    expect(await screen.findByText(/Some asks couldn't be loaded right now/)).toBeTruthy();
    cleanup();

    inbox.feed.mockRejectedValueOnce(new Error('down'));
    useInboxStore.setState({ items: [] });
    renderPage();
    inbox.feed.mockResolvedValue({ items: [ask()], counts: EMPTY_COUNTS });
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(screen.getAllByTestId('needs-you-row')).toHaveLength(1));
  });
});
