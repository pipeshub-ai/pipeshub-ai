/**
 * BGUI-10: the open job's Brief reads as the job's task in a few plain sentences (design `worker-panel.tsx`,
 * design-02-job-brief.png), from the parts the harness kept: never the prompt composed for the engine (the budget
 * sentence to the model, the notes heading, a carried call's raw JSON). A recorded IPL report hand-off is the case.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import i18n from '@/lib/__tests__/test-i18n';
import type { JobBrief } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief';
import type { RunBudgetView, RunTimelineView } from '@/chat/types';

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/lib/api', () => ({
  apiClient: { get: (...args: unknown[]) => api.get(...args), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSEGet: () => new Promise<void>(() => undefined),
}));

import { JobBriefBody } from '../job-brief';
import { RunDetailsView, costWorthShowing } from '../run-details';
import { briefView } from '@/lib/harness-jobs/job-brief';

const REQUEST = 'Can you prepare a pdf report of ipl 2026 event';
const NOTES =
  'Prepare a **PDF report** on the IPL 2026 season:\n\n- the winner and the final\n- the top run scorers and wicket takers';
// What the engine was told: the Brief must show none of what follows the request.
const COMPOSED =
  `${REQUEST}\n\nThis task's time and money: deliver your result by 10:12 UTC (about 56 minutes from now), with at most USD 5.00 of model use.\n\n` +
  `Notes from the chat agent that handed this task to you:\n${NOTES}\n\nTool calls this chat already made before handing over, with their results.\n` +
  '- load_skill({"name": "pdf"}):\n  # PDF skill';
// A due time an hour on reads as a time of day only while it stays today: from 23:00 it is tomorrow and reads with
// its date, so the clock is pinned to a morning (only Date is faked, so the renderer's timers stay real).
vi.useFakeTimers({ toFake: ['Date'] });
vi.setSystemTime(new Date(2026, 10, 12, 9, 30, 0));
const DUE = new Date();
DUE.setHours(DUE.getHours() + 1, 12, 0, 0);

const brief = (task: Partial<NonNullable<JobBrief['task']>> | null = {}, goal: string | null = COMPOSED): JobBrief => ({
  runId: 'job-1',
  parentRunId: 'run-1',
  sessionId: 'conv-1',
  agent: { kind: 'engine', engine: 'claude_code' },
  goal,
  task:
    task === null
      ? null
      : {
          request: REQUEST,
          notes: NOTES,
          earlier: '',
          fileName: null,
          dueAt: DUE.toISOString(),
          maxCostUsd: 5,
          carried: [
            { tool: 'search_tools', failed: false },
            { tool: 'load_skill', failed: false, skill: 'pdf' },
          ],
          ...task,
        },
  sent: { files: [], sources: [], sourcesComplete: true, knowledge: { apps: [], collections: [] }, skills: [], continuesRunId: null },
  withheld: [{ what: 'conversation', reason: 'task_only' }],
  reach: { internet: 'none', sandbox: true, tools: [], allowedWithoutAsking: 0 },
});

/** What a person reads: an icon's ligature name is no text of theirs (it is aria-hidden and never selected). */
function read(el: Element): string {
  const copy = el.cloneNode(true) as Element;
  copy.querySelectorAll('[aria-hidden="true"]').forEach((icon) => icon.remove());
  return copy.textContent ?? '';
}

function show(value: JobBrief) {
  api.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/brief')) return { data: value };
    if (url.endsWith('/brief/history')) return { data: { runId: 'job-1', startedAs: REQUEST, changes: [] } };
    return { data: {} };
  });
  render(
    <Theme>
      <JobBriefBody run={{ sessionId: 'conv-1', runId: 'job-1' } as never} />
    </Theme>,
  );
  return waitFor(() => screen.getByTestId('job-brief'));
}

beforeEach(() => {
  api.get.mockReset();
  void i18n.changeLanguage('en-US');
});
afterEach(cleanup);

describe('BGUI-10: the Brief is the task, not the prompt', () => {
  it('opens on the task as prose, the request in the person’s words, and the limits as one line of facts', async () => {
    const body = await show(brief());
    const task = within(body).getByTestId('job-brief-goal');
    // The notes' markdown is rendered: a list, bold text, no asterisks or hyphens left.
    expect(task.querySelectorAll('li')).toHaveLength(2);
    expect(task.querySelector('strong')?.textContent).toBe('PDF report');
    expect(task.textContent).not.toContain('**');
    expect(screen.getByTestId('job-brief-asked').textContent).toBe(`You asked: “${REQUEST}”`);
    const limits = read(screen.getByTestId('job-brief-limits'));
    expect(limits).toMatch(/^Due by \d{1,2}:12\s?(AM|PM) · Up to USD\s5\.00$/);
    // Nothing of the composed prompt reaches the tab.
    const text = read(body);
    for (const leaked of ['time and money', 'Notes from the chat agent', 'Tool calls this chat', '{"name"', 'load_skill', 'UTC']) {
      expect(text, leaked).not.toContain(leaked);
    }
  });

  it('folds what it started with: the carried calls by their plain names, no meta tool, no arguments', async () => {
    await show(brief({ carried: [{ tool: 'search_tools', failed: false }, { tool: 'load_skill', failed: false, skill: 'pdf' }, { tool: 'web_search', failed: false }, { tool: 'web_search', failed: false }, { tool: 'jira__search_issues', failed: true }] }));
    expect(screen.queryByTestId('job-brief-started')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'What it started with' }));
    const started = screen.getByTestId('job-brief-started');
    const rows = Array.from(started.querySelectorAll('li')).map(read);
    expect(rows).toEqual(['Loaded the PDF skill', 'Searched the web (×2)', "Jira · Search issuesDidn't work"]);
    expect(started.textContent).toContain('Already done before the hand-off');
  });

  it('a task whose notes only repeated the request shows the request alone, the file it delivers, and no fold with nothing in it', async () => {
    await show(brief({ notes: '', carried: [], dueAt: null, maxCostUsd: null, fileName: 'ipl-2026.pdf' }));
    expect(screen.getByTestId('job-brief-goal').textContent).toBe(REQUEST);
    expect(screen.queryByTestId('job-brief-asked')).toBeNull();
    expect(read(screen.getByTestId('job-brief-limits'))).toBe('Delivers ipl-2026.pdf');
    expect(screen.queryByRole('button', { name: 'What it started with' })).toBeNull();
  });

  it('a long task opens on its first lines with Show more', async () => {
    await show(brief({ notes: `${'The season had many close matches. '.repeat(30)}` }));
    const more = screen.getByTestId('job-brief-more');
    expect(more.textContent).toBe('Show more');
    expect(more.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(more);
    expect(more.textContent).toBe('Show less');
  });

  it('a job kept before the parts were shows the goal the server cut to the request; an erased one says so', async () => {
    await show(brief(null, REQUEST));
    expect(screen.getByTestId('job-brief-goal').textContent).toBe(REQUEST);
    expect(screen.queryByTestId('job-brief-limits')).toBeNull();
    cleanup();
    await show(brief(null, null));
    expect(screen.getByTestId('job-brief-goal').textContent).toBe('Its task was erased with this chat’s content.'.replace('’', "'"));
  });

  it('a due time on another day carries its date', () => {
    const later = new Date(Date.now() + 3 * 86_400_000).toISOString();
    const t = (key: string, vars?: Record<string, unknown>) => i18n.t(key, vars) as string;
    const view = briefView(brief({ dueAt: later, maxCostUsd: null }), t, { locale: 'en-US' });
    expect(view.task.limits[0]).toMatch(/^Due by [A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}\s?(AM|PM)$/);
  });
});

describe('BGUI-10: How it came out says what it cost only once it says something', () => {
  const budget = (usdMicros: number, tokens: number, exhausted = false): RunBudgetView => ({
    cap: { usdMicros: 5_000_000, tokens: null },
    committed: { usdMicros, tokens },
    reserved: { usdMicros: 0, tokens: 0 },
    available: { usdMicros: 5_000_000 - usdMicros, tokens: null },
    exhausted,
  });

  it('no cost row for a job that has spent nothing; the row once it has, or hit its cap', () => {
    expect(costWorthShowing(budget(0, 0))).toBe(false);
    expect(costWorthShowing(null)).toBe(false);
    expect(costWorthShowing(budget(3_200, 1_013))).toBe(true);
    expect(costWorthShowing(budget(0, 0, true))).toBe(true);
    const timeline = { budget: budget(0, 0), sites: [], secretsHidden: null, engine: 'claude_code', children: [], status: 'running' } as unknown as RunTimelineView;
    const { container } = render(
      <Theme>
        <RunDetailsView run={{ sessionId: 'conv-1', runId: 'job-1' } as never} timeline={timeline} part="spend" />
      </Theme>,
    );
    expect(container.querySelector('[data-testid="run-cost"]')).toBeNull();
  });
});
