/**
 * BGUI-11 (an IPL report job): the report under a job's card printed the engine's whole reply, a raw
 * `::artifact[…]{…}` line and bare citation numbers included, the chat's answer then said it again, and "Changed 14
 * files" counted the engine's notes and page renders where the panel said 7.
 *
 * Now the report is how it ended plus what it delivered, as the answer's own file cards, and its working files folded
 * into one line that opens the job's new Outputs tab; the engine's own words show only in its panel, through the
 * shared answer rendering. Delivered and working are the server's one classification, so every count agrees.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { ActivityFile, MessagePart, RunDigestView } from '../../../../types';

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet: vi.fn(async () => undefined) }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const { useChatStore } = await import('../../../../store');
const { TurnJobs } = await import('../turn-jobs');
const { JobReply } = await import('../job-reply');
const { useJobsStore } = await import('../data');
const { useActivityPanelStore, jobPanelSectionOf, JOB_PANEL_SECTIONS } = await import('../../../activity-panel/activity-panel-store');
const { JobOutputs } = await import('../../../activity-panel/job-outputs');
const { jobDeliverables, jobFiles, splitChangedFiles } = await import('@/lib/harness-jobs/job-files');
const { toActivityView } = await import('../../../../activity');

const h = React.createElement;
const SESSION = 'ses-ipl';
const initialState = useChatStore.getState();

/** A recorded IPL reply as the job's timeline kept it: findings, process talk, the directive Node appends, citations. */
const IPL_REPLY = [
  '**ipl_2026_report.pdf** is a four-page, A4 season review.',
  '',
  'RCB defended the title by beating Gujarat Titans by five wickets [1](https://www.iplt20.com/a) [2](https://www.espncricinfo.com/b).',
  '',
  "The PDF's layout check passed (`layout: clean`), and I visually inspected the rendered pages.",
  '',
  '::artifact[ipl_2026_report.pdf](record:rec-ipl){application/pdf|doc-ipl|rec-ipl|DOCUMENT|1}',
].join('\n');

const DIGEST: RunDigestView = {
  outcome: 'answered',
  actions: { total: 0, failed: 0, refused: 0, tools: [] },
  commands: { total: 7, failed: 2 },
  records: { read: 0, changed: 0 },
  artifacts: [{ name: 'ipl_2026_report.pdf', recordId: 'rec-ipl', mimeType: 'application/pdf', sizeBytes: 48_213, artifactType: 'DOCUMENT', version: 1 }],
  files: { added: 7, modified: 0, deleted: 0, working: 6 },
  approvals: { asked: 0, approved: 0, denied: 0, unanswered: 0 },
  questions: { asked: 0, open: false },
  delegated: 0,
  cost: null,
  durationMs: 387_000,
};

const job = (runId: string, patch: Partial<MessagePart> = {}): MessagePart => ({
  type: 'sub_agent',
  runId,
  parentRunId: 'run-root',
  codeTaskGoal: 'Create a polished, concise PDF report on IPL 2026',
  codeTaskHandoff: { trigger: 'upfront', reason: 'research', engine: 'codex' },
  status: 'completed',
  durationMs: 387_000,
  parts: [],
  ...patch,
});

const timeline = (runId: string, digest: RunDigestView = DIGEST) => ({
  runId,
  parentRunId: 'run-root',
  status: 'completed',
  parts: [{ type: 'text', isFinal: true, content: IPL_REPLY }],
  engine: null,
  approvals: [],
  children: [],
  merge: null,
  budget: null,
  digest,
});

let timelines: Record<string, unknown>;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, run: null, focusSection: null, jobSection: null });
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
  timelines = {};
  apiClient.get.mockImplementation(async (url: string) => {
    const id = /\/runs\/([^/]+)\/timeline$/.exec(url)?.[1];
    if (id && timelines[id]) return { data: timelines[id] };
    if (url.endsWith('/interactions/authority')) return { data: { approve: true, deny: true } };
    throw new Error(`unexpected GET ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const view = (node: React.ReactElement) => render(h(Theme, null, node));

describe('BGUI-11: the report under the card never shows the engine’s raw reply', () => {
  it('the IPL job: how it ended, its PDF as the answer’s file card, its six working files in one line', async () => {
    timelines['job-ipl'] = timeline('job-ipl');
    view(h(TurnJobs, { jobs: [job('job-ipl')] }));
    const report = await screen.findByTestId('job-report');
    expect(within(report).getByTestId('job-report-outcome').textContent).toBe(' · finished in 6m 27s');
    const card = await within(report).findByTestId('artifact-card');
    expect(card.textContent).toContain('ipl_2026_report.pdf');
    expect(within(card).getByRole('button', { name: 'Download artifact' })).toBeTruthy();
    expect(within(report).getByRole('button', { name: '6 working files' })).toBeTruthy();
    // None of its reply: no directive, no process talk, no findings (the chat's answer says those once).
    expect(report.textContent).not.toMatch(/::artifact|layout: clean|visually inspected|RCB defended/);
    expect(screen.queryByTestId('job-report-answer')).toBeNull();
  });

  it('a file the turn’s answer already shows as a card is left to it; the working line stays', async () => {
    timelines['job-ipl'] = timeline('job-ipl');
    view(h(TurnJobs, { jobs: [job('job-ipl')], shownFiles: new Set(['rec-ipl']) }));
    const report = await screen.findByTestId('job-report');
    await within(report).findByRole('button', { name: '6 working files' });
    expect(within(report).queryByTestId('artifact-card')).toBeNull();
  });

  it('the working line opens the job’s Outputs tab', async () => {
    timelines['job-ipl'] = timeline('job-ipl');
    view(h(TurnJobs, { jobs: [job('job-ipl')] }));
    fireEvent.click(await screen.findByRole('button', { name: '6 working files' }));
    expect(useActivityPanelStore.getState().run?.runId).toBe('job-ipl');
    expect(useActivityPanelStore.getState().jobSection).toBe('outputs');
  });

  it('of several jobs each shows its own delivered file, and none its reply', async () => {
    timelines['job-a'] = timeline('job-a');
    timelines['job-b'] = timeline('job-b', {
      ...DIGEST,
      artifacts: [{ name: 'standings.csv', recordId: 'rec-csv', mimeType: 'text/csv' }],
      files: { added: 1, modified: 0, deleted: 0, working: 0 },
    });
    view(h(TurnJobs, { jobs: [job('job-a'), job('job-b')] }));
    await screen.findAllByTestId('job-report');
    const cards = await screen.findAllByTestId('artifact-card');
    expect(cards.map((c) => c.textContent)).toEqual([expect.stringContaining('ipl_2026_report.pdf'), expect.stringContaining('standings.csv')]);
    expect(document.body.textContent).not.toMatch(/::artifact|visually inspected/);
    // Only the job with working files has the line.
    expect(screen.getAllByTestId('job-report-working')).toHaveLength(1);
  });
});

describe('BGUI-11: the engine’s own words, where they show, go through the shared answer rendering', () => {
  it('its file directive never shows and its citation links become numbered chips, not links', () => {
    view(h(JobReply, { text: IPL_REPLY }));
    const reply = screen.getByTestId('job-reply');
    expect(reply.textContent).not.toContain('::artifact');
    expect(reply.textContent).not.toContain('rec-ipl');
    expect(reply.textContent).toContain('four-page');
    // BGUI-12: the chip names its source (here the link's site, no citation stored) before its number.
    expect(reply.textContent).toMatch(/five wickets\s*iplt20\s*1/);
    expect(reply.textContent).not.toContain("](https");
  });

  it('a reply that is only a directive draws nothing', () => {
    view(h(JobReply, { text: '::artifact[a.pdf](record:r){application/pdf|d|r|DOCUMENT|1}' }));
    expect(screen.queryByTestId('job-reply')).toBeNull();
  });
});

const files = (paths: string[], delivered: string[] = []): ActivityFile[] =>
  paths.map((path) => ({ path, change: 'added', diff: '+x', ...(delivered.includes(path) ? { delivered: true as const } : {}) }));

describe('BGUI-11: the Outputs tab', () => {
  const outputs = jobDeliverables([], DIGEST);

  it('draws what it delivered on top as file cards, then its working files folded, each with its diff one click down', () => {
    const changed = files(['notes/progress.md', 'make_report.py', 'out/ipl_2026_report.pdf'], ['out/ipl_2026_report.pdf']);
    view(h(JobOutputs, { outputs, files: changed, live: false, phone: false, runId: 'job-ipl', onMachine: vi.fn() }));
    const delivered = screen.getByTestId('job-outputs-delivered');
    expect(delivered.textContent).toContain('Delivered file');
    expect(within(delivered).getAllByTestId('artifact-card')).toHaveLength(1);
    const fold = screen.getByRole('button', { name: '2 working files' });
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryAllByTestId('activity-file')).toHaveLength(0);
    fireEvent.click(fold);
    // The delivered PDF is a card, not a working file.
    expect(screen.getAllByTestId('activity-file').map((f) => f.textContent)).toEqual([
      expect.stringContaining('notes/progress.md'),
      expect.stringContaining('make_report.py'),
    ]);
  });

  it('offers the whole workspace on Machine', () => {
    const onMachine = vi.fn();
    view(h(JobOutputs, { outputs, files: [], live: false, phone: false, runId: 'job-ipl', onMachine }));
    fireEvent.click(screen.getByRole('button', { name: 'See the whole workspace in Machine' }));
    expect(onMachine).toHaveBeenCalledOnce();
  });

  it('says in plain words that files will show while it works, and that it made none once it ended', () => {
    const { rerender } = view(h(JobOutputs, { outputs: [], files: [], live: true, phone: false, runId: 'j' }));
    expect(screen.getByTestId('job-outputs-empty').textContent).toBe('Files it makes will show here');
    rerender(h(Theme, null, h(JobOutputs, { outputs: [], files: [], live: false, phone: false, runId: 'j' })));
    expect(screen.getByTestId('job-outputs-empty').textContent).toBe("It didn't make any files");
  });

  it('on a phone its controls are 44 px', () => {
    view(h(JobOutputs, { outputs, files: files(['notes/a.md']), live: false, phone: true, runId: 'j', onMachine: vi.fn() }));
    expect(screen.getByRole('button', { name: '1 working file' }).style.minHeight).toBe('44px');
    expect(screen.getByRole('button', { name: 'Download artifact' }).style.minHeight).toBe('44px');
  });

  it('is named by the URL as `section=outputs`, sits between Activity and Machine, and an old Files link lands on it', () => {
    expect(JOB_PANEL_SECTIONS).toEqual(['brief', 'activity', 'outputs', 'machine']);
    expect(jobPanelSectionOf('outputs')).toBe('outputs');
    expect(jobPanelSectionOf('files')).toBe('outputs');
    expect(jobPanelSectionOf('terminal')).toBe('machine');
  });
});

describe('BGUI-11: one classification for every surface', () => {
  it('delivered files come from its artifact frames, then its summary, each once, a discarded draft never', () => {
    const frame = (text: string, data: Record<string, unknown>) => ({ tool: 'code_task', taskId: 'j', kind: 'artifact', text, data });
    const list = jobDeliverables(
      [
        frame('ipl_2026_report.pdf', { artifactId: 'rec-ipl', version: 1, mimeType: 'application/pdf', lifecycle: 'draft' }),
        frame('ipl_2026_report.pdf', { artifactId: 'rec-ipl', version: 2, mimeType: 'application/pdf', sizeBytes: 9, lifecycle: 'final' }),
        frame('draft.md', { artifactId: 'rec-d', lifecycle: 'discarded' }),
      ],
      { ...DIGEST, artifacts: [...DIGEST.artifacts, { name: 'out/standings.csv' }] },
    );
    expect(list.map((a) => [a.fileName, a.recordId, a.version])).toEqual([
      ['ipl_2026_report.pdf', 'rec-ipl', 2],
      ['standings.csv', undefined, undefined],
    ]);
    expect(list[0].mimeType).toBe('application/pdf');
  });

  it('the activity view keeps the server’s mark: without it the panel counted the deliverable as a working file', () => {
    const parsed = toActivityView({
      runId: 'j',
      status: 'completed',
      engine: true,
      commands: [],
      omittedCommands: 0,
      tools: [],
      tasks: [],
      files: [
        { path: 'out/r.pdf', change: 'added', delivered: true },
        { path: 'notes/a.md', change: 'added' },
      ],
      followups: [],
    });
    expect(parsed?.files.map((f) => [f.path, f.delivered === true])).toEqual([
      ['out/r.pdf', true],
      ['notes/a.md', false],
    ]);
  });

  it('the working count is the server’s; the panel’s list splits by the server’s mark, so the two agree', () => {
    const changed = files(['a.md', 'b.py', 'out/r.pdf'], ['out/r.pdf']);
    const split = splitChangedFiles(changed);
    expect(split.delivered.map((f) => f.path)).toEqual(['out/r.pdf']);
    expect(split.working).toHaveLength(2);
    expect(jobFiles([], { ...DIGEST, files: { added: 3, modified: 0, deleted: 0, working: 2 } }).working).toBe(split.working.length);
    // An older server sends no count: the report says nothing rather than count every changed file again.
    expect(jobFiles([], { ...DIGEST, files: { added: 3, modified: 0, deleted: 0 } }).working).toBeNull();
  });
});
