/**
 * UX5 FE-2..FE-7 and FU-8: the activity panel's states, auto-scroll, live
 * region, windowed output, keyboard and diff links, and the header toggle.
 * The panel is read-only: no case finds an input in it.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { SSEGetOptions } from '@/lib/api/streaming';
import type { ActivityCommand, ActivityView, DurableAgentRunHandle } from '@/chat/types';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  /** The stream serves these events, then stays open until the panel aborts it. */
  events: [] as Array<{ event: string; data: unknown }>,
  opened: 0,
  urls: [] as string[],
}));
vi.mock('@/lib/api', () => ({
  apiClient: { get: (...args: unknown[]) => api.get(...args), post: vi.fn() },
  streamSSEGet: (url: string, options: SSEGetOptions) => {
    api.opened += 1;
    api.urls.push(url);
    for (const event of api.events) options.onEvent(event);
    return new Promise<void>((resolve) => options.signal?.addEventListener('abort', () => resolve(), { once: true }));
  },
}));

import { ActivityPanelView } from '../activity-panel/activity-panel';
import { ActivityPanelHost, ViewActivityButton, useActivityPanelLayout } from '../activity-panel/activity-panel-host';
import { useActivityPanelStore } from '../activity-panel/activity-panel-store';
import type { RunActivityState } from '@/chat/hooks/use-run-activity';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };
const initialChat = useChatStore.getState();

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useFeatureFlagsStore.setState({ flags: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, focusSection: null, run: null });
  api.events = [];
  api.opened = 0;
  api.urls = [];
  api.get.mockReset();
});
afterEach(() => {
  window.localStorage.clear();
  cleanup();
  vi.useRealTimers();
});

const command = (patch: Partial<ActivityCommand> = {}): ActivityCommand => ({
  commandId: 'cmd-1',
  source: 'run_code',
  command: 'pytest -q',
  status: 'running',
  exitCode: null,
  startedAt: 1_000,
  durationMs: null,
  stdout: '',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
  ...patch,
});

const view = (patch: Partial<ActivityView> = {}): ActivityView => ({
  runId: RUN.runId,
  status: 'running',
  engine: false,
  commands: [],
  omittedCommands: 0,
  tools: [],
  tasks: [],
  files: [],
  followups: [],
  ...patch,
});

const live = (v: ActivityView | null, connection: RunActivityState['connection'] = 'live'): RunActivityState => ({ view: v, connection });

function renderView(state: RunActivityState, onClose = vi.fn()) {
  const utils = render(
    <Theme>
      <ActivityPanelView run={RUN} state={state} variant="pane" onClose={onClose} />
    </Theme>,
  );
  const rerenderState = (next: RunActivityState) =>
    utils.rerender(
      <Theme>
        <ActivityPanelView run={RUN} state={next} variant="pane" onClose={onClose} />
      </Theme>,
    );
  return { ...utils, onClose, rerenderState };
}

function lines(count: number, prefix = 'line'): string {
  return Array.from({ length: count }, (_, i) => `${prefix} ${i + 1}`).join('\n') + '\n';
}

function expectReadOnly(container: HTMLElement) {
  expect(container.querySelectorAll('input, textarea, select, [contenteditable="true"]')).toHaveLength(0);
}

/** jsdom has no layout: the output block's geometry is set by hand. */
function geometry(el: HTMLElement, client = 280) {
  const box = { content: 0, top: 0 };
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => client });
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => box.content });
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => box.top,
    set: (value: number) => {
      box.top = value;
    },
  });
  return box;
}

describe('FE-2: the panel\'s states', () => {
  it('empty: "Nothing has run yet", read-only', () => {
    const { container } = renderView(live(view()));
    const panel = screen.getByRole('complementary', { name: 'Activity' });
    // BETA: the agent harness is in beta, so the panel's title carries the badge (its accessible name does not).
    expect(within(panel).getByText('Beta')).toBeTruthy();
    expect(screen.getByText('Nothing has run yet.')).toBeTruthy();
    expect(screen.getByText('Commands, tool calls and changed files appear here as the agent works.')).toBeTruthy();
    expect(screen.getByText('What the agent ran in its sandbox. Read-only.')).toBeTruthy();
    expectReadOnly(container);
  });

  it('running, finished and failed commands: `$ command`, status, exit code, duration, stderr tinted', () => {
    const { container } = renderView(
      live(
        view({
          commands: [
            command({ commandId: 'a', command: 'ls', status: 'completed', exitCode: 0, durationMs: 1_300, stdout: 'a.py\n' }),
            command({ commandId: 'b', command: 'pytest -q', status: 'failed', exitCode: 2, durationMs: 850, stdout: '1 failed\n', stderr: 'E assert 41 == 42\n' }),
            command({ commandId: 'c', command: 'npm test' }),
          ],
        }),
      ),
    );
    const blocks = screen.getAllByTestId('activity-command');
    expect(within(blocks[0]!).getByText('Exit 0')).toBeTruthy();
    expect(within(blocks[0]!).getByText('1.3 sec')).toBeTruthy();
    expect(within(blocks[1]!).getByText('Exit 2')).toBeTruthy();
    expect(within(blocks[1]!).getByText('850 ms')).toBeTruthy();
    const stderr = blocks[1]!.querySelector('[data-stream="stderr"]') as HTMLElement;
    expect(stderr.textContent).toBe('E assert 41 == 42');
    expect(stderr.style.color).not.toBe((blocks[1]!.querySelector('[data-stream="stdout"]') as HTMLElement).style.color);
    expect(within(blocks[2]!).getByText('Running')).toBeTruthy();
    expect(within(blocks[2]!).getByText('Waiting for output…')).toBeTruthy();
    expect(within(blocks[2]!).getByText('npm test')).toBeTruthy();
    expectReadOnly(container);
  });

  it('says how much output was not shown live and offers the full log, which opens as text', async () => {
    api.get.mockResolvedValue({ data: 'full line 1\nfull line 2\n' });
    renderView(live(view({ commands: [command({ status: 'completed', exitCode: 0, liveDropped: 12_288, truncated: true, hasLog: true, stdout: 'tail\n' })] })));
    expect(screen.getByText('12 kB of output was not shown live. The full log has it.')).toBeTruthy();
    expect(screen.getByText('Earlier output is not shown here. The full log has it.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Full log of pytest -q' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('full line 2')).toBeTruthy();
    expect(api.get.mock.calls[0]?.[0]).toBe('/api/v1/agents/agent-1/conversations/conv-1/runs/run-1/commands/cmd-1/log');
  });

  it('an approval held shows why and points to the card; a denied call shows its rule', () => {
    renderView(
      live(
        view({
          status: 'waiting',
          tools: [
            {
              toolCallId: 't-1',
              name: 'slack.send_message',
              displayName: 'Send message',
              status: 'awaiting_approval',
              reason: { rule: 'flow_external_write', sensitivity: 'private', sources: [], moreSources: 0, conversationGrant: false },
            },
            { toolCallId: 't-2', name: 'gmail.send', status: 'denied', refusal: { rule: 'egress_allowlist' } },
            { toolCallId: 't-3', name: 'web.fetch', status: 'completed' },
          ],
        }),
      ),
    );
    expect(screen.getByText('Waiting for you')).toBeTruthy();
    const [held, denied, done] = screen.getAllByTestId('activity-tool');
    expect(within(held!).getByText('Waiting for approval')).toBeTruthy();
    expect(within(held!).getByTestId('activity-tool-reason').textContent).toMatch(/Decide on the approval card in the chat\.$/);
    expect(within(held!).getByTestId('activity-tool-reason').textContent!.length).toBeGreaterThan('Decide on the approval card in the chat.'.length + 5);
    expect(within(denied!).getByText('Denied')).toBeTruthy();
    expect(within(denied!).getByText('Refused by the rule “egress_allowlist”.')).toBeTruthy();
    expect(within(done!).queryByTestId('activity-tool-reason')).toBeNull();
  });

  it('reconnecting keeps the last view on screen and says so; unavailable explains itself', () => {
    const { rerenderState } = renderView(live(view({ commands: [command({ stdout: 'still here\n' })] }), 'reconnecting'));
    expect(screen.getByText('Reconnecting…')).toBeTruthy();
    expect(screen.getByText('still here')).toBeTruthy();
    rerenderState(live(null, 'unavailable'));
    expect(screen.getByText("Activity isn't available for this run.")).toBeTruthy();
    rerenderState(live(null, 'connecting'));
    expect(screen.getByText('Loading activity…')).toBeTruthy();
  });

  it('a finished run says Finished', () => {
    renderView(live(view({ status: 'completed', commands: [command({ status: 'completed', exitCode: 0 })] })));
    expect(screen.getByText('Finished')).toBeTruthy();
    expect(screen.getByText('No output.')).toBeTruthy();
  });
});

describe('FU-8: the plan is the next steps while the run works', () => {
  it('lists tasks with their status, captioned as what comes next', () => {
    renderView(
      live(
        view({
          tasks: [
            { taskId: 'task-1', title: 'Read the failing test', status: 'completed' },
            { taskId: 'task-2', title: 'Fix the rounding', status: 'in_progress' },
            { taskId: 'task-3', title: 'Run the suite', status: 'pending' },
          ],
        }),
      ),
    );
    const plan = screen.getByTestId('activity-section-plan');
    expect(within(plan).getByText('What the agent plans to do next.')).toBeTruthy();
    expect(within(plan).getAllByTestId('activity-task').map((row) => row.querySelector('.rt-Text')?.textContent)).toEqual([
      'Done: Read the failing test',
      'In progress: Fix the rounding',
      'To do: Run the suite',
    ]);
  });
});

describe('FE-3: auto-scroll', () => {
  it('follows new output at the bottom, stops when the reader scrolls up, and "Jump to latest" resumes', () => {
    const at = (n: number) => live(view({ commands: [command({ stdout: lines(n) })] }));
    const { rerenderState } = renderView(at(100));
    const output = screen.getByRole('group', { name: 'Output of pytest -q' });
    const box = geometry(output);

    box.content = 120 * 18;
    rerenderState(at(120));
    expect(box.top).toBe(120 * 18);

    box.top = 400;
    fireEvent.scroll(output);
    box.content = 140 * 18;
    rerenderState(at(140));
    expect(box.top).toBe(400);
    const jump = screen.getByRole('button', { name: 'Jump to latest' });

    fireEvent.click(jump);
    expect(box.top).toBe(140 * 18);
    expect(screen.queryByRole('button', { name: 'Jump to latest' })).toBeNull();

    box.content = 160 * 18;
    rerenderState(at(160));
    expect(box.top).toBe(160 * 18);
  });
});

describe('FE-4: the live region', () => {
  it('announces a command starting and finishing with its exit code, an approval, the run\'s end; never output', () => {
    const { rerenderState } = renderView(live(view()));
    const region = screen.getByTestId('activity-live');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.textContent).toBe('');

    rerenderState(live(view({ commands: [command()] })));
    expect(region.textContent).toBe('Command started: pytest -q');

    rerenderState(live(view({ commands: [command({ stdout: 'SECRET OUTPUT LINE\n' })] })));
    expect(region.textContent).not.toContain('SECRET OUTPUT LINE');
    expect(region.textContent).toBe('Command started: pytest -q');

    rerenderState(live(view({ commands: [command({ status: 'completed', exitCode: 0, stdout: 'SECRET OUTPUT LINE\n' })] })));
    expect(region.textContent).toBe('Command finished with exit code 0: pytest -q');

    rerenderState(
      live(
        view({
          commands: [command({ status: 'completed', exitCode: 0 })],
          tools: [{ toolCallId: 't', name: 'jira.create', displayName: 'Create issue', status: 'awaiting_approval' }],
        }),
      ),
    );
    expect(region.textContent).toBe('Approval needed: Create issue');

    rerenderState(live(view({ status: 'completed', commands: [command({ status: 'completed', exitCode: 0 })] })));
    expect(region.textContent).toBe('The run finished.');
  });
});

describe('FE-5: long output is windowed', () => {
  it('a 10,000-line output keeps at most 200 rows in the DOM, and the newest line is one of them', () => {
    renderView(live(view({ commands: [command({ status: 'completed', exitCode: 0, stdout: lines(10_000) })] })));
    const rows = screen.getByRole('group', { name: 'Output of pytest -q' }).querySelectorAll('[data-stream]');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThanOrEqual(200);
    expect(rows[rows.length - 1]?.textContent).toBe('line 10000');
  });

  it('scrolled to the top, the window moves with it', () => {
    renderView(live(view({ commands: [command({ status: 'completed', exitCode: 0, stdout: lines(10_000) })] })));
    const output = screen.getByRole('group', { name: 'Output of pytest -q' });
    const box = geometry(output);
    box.content = 10_000 * 18;
    box.top = 0;
    fireEvent.scroll(output);
    const rows = output.querySelectorAll('[data-stream]');
    expect(rows[0]?.textContent).toBe('line 1');
    expect(rows.length).toBeLessThanOrEqual(200);
  });
});

describe('FE-6, FE-7: keyboard, labels and the diff view', () => {
  it('sections are buttons that collapse; output blocks are focusable and labelled; files open U1\'s diff', () => {
    renderView(
      live(
        view({
          commands: [command({ status: 'completed', exitCode: 0, stdout: 'ok\n' })],
          files: [{ path: 'src/report.py', change: 'modified', diff: '--- a/src/report.py\n+++ b/src/report.py\n@@ -1 +1 @@\n-x = 41\n+x = 42\n' }],
        }),
      ),
    );
    const commandsHeader = screen.getByRole('button', { name: /Commands/ });
    expect(commandsHeader.getAttribute('aria-expanded')).toBe('true');
    const output = screen.getByRole('group', { name: 'Output of pytest -q' });
    expect(output.tabIndex).toBe(0);
    fireEvent.click(commandsHeader);
    expect(commandsHeader.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('group', { name: 'Output of pytest -q' })).toBeNull();

    // Named by its own words (label in name, CJFIX-B F5): the change and the path.
    const file = screen.getByRole('button', { name: /src\/report\.py$/ });
    expect(file.getAttribute('aria-label')).toBeNull();
    expect(file.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(file);
    expect(file.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('Lines added: 1. Lines removed: 1.')).toBeTruthy();
    expect(screen.getByText('+x = 42')).toBeTruthy();
  });

  it('Escape closes the panel', () => {
    const { onClose } = renderView(live(view({ commands: [command({ stdout: 'x\n' })] })));
    fireEvent.keyDown(screen.getByRole('group', { name: 'Output of pytest -q' }), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('CTL-3: "View activity" on a message opens the drawer for that message\'s run', () => {
  const OLDER: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-0' };

  function Harness({ isMobile = false, runs = [RUN] }: { isMobile?: boolean; runs?: DurableAgentRunHandle[] }) {
    const layout = useActivityPanelLayout(isMobile, false);
    return (
      <Theme>
        {runs.map((run) => <ViewActivityButton key={run.runId} run={run} isMobile={isMobile} onClosePreview={() => {}} />)}
        {layout.visible && layout.run ? <ActivityPanelHost run={layout.run} isMobile={isMobile} /> : null}
      </Theme>
    );
  }

  function withRun(patch: Record<string, unknown> = {}) {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true, threadAgentId: 'agent-1', durableRun: RUN, isStreaming: true, ...patch });
    store.setActiveSlot(slotId);
  }

  it('CTL-5: shown whatever an older Labs page stored for the retired switch', () => {
    withRun();
    useFeatureFlagsStore.setState({ flags: { ENABLE_AGENT_ACTIVITY_PANEL: false } });
    render(<Harness />);
    expect(screen.getByRole('button', { name: 'View activity' })).toBeTruthy();
  });

  it('the drawer never opens by itself, even where an older build remembered it open', () => {
    window.localStorage.setItem('pipeshub.activityPanel.open', '1');
    withRun();
    render(<Harness />);
    expect(screen.queryByRole('complementary', { name: 'Activity' })).toBeNull();
    expect(api.opened).toBe(0);
  });

  it('opens on click (the stream only then), says so with aria-expanded, and Escape returns focus to the control', async () => {
    withRun();
    api.events = [{ event: 'snapshot', data: view({ commands: [command({ stdout: 'collecting\n' })] }) }];
    render(<Harness />);
    const control = screen.getByRole('button', { name: 'View activity' });
    expect(control.getAttribute('aria-expanded')).toBe('false');
    expect(control.getAttribute('aria-controls')).toBe('chat-activity-panel');

    fireEvent.click(control);
    expect(control.getAttribute('aria-expanded')).toBe('true');
    const panel = await screen.findByRole('complementary', { name: 'Activity' });
    expect(document.activeElement).toBe(panel);
    expect(await within(panel).findByText('collecting')).toBeTruthy();
    expect(api.opened).toBe(1);

    fireEvent.keyDown(within(panel).getByRole('group', { name: 'Output of pytest -q' }), { key: 'Escape' });
    expect(screen.queryByRole('complementary', { name: 'Activity' })).toBeNull();
    expect(document.activeElement).toBe(control);
    expect(control.getAttribute('aria-expanded')).toBe('false');
    expect(window.localStorage.getItem('pipeshub.activityPanel.open')).not.toBe('0');
  });

  it('an older message\'s control shows that message\'s run, not the latest', async () => {
    withRun({ durableRun: null, isStreaming: false, lastDurableRun: RUN });
    api.events = [{ event: 'snapshot', data: view({ status: 'completed' }) }];
    render(<Harness runs={[OLDER, RUN]} />);
    const [older] = screen.getAllByRole('button', { name: 'View activity' });
    fireEvent.click(older);
    await screen.findByRole('complementary', { name: 'Activity' });
    expect(api.urls.at(-1)).toContain('run-0');
    expect(older.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getAllByRole('button', { name: 'View activity' })[1].getAttribute('aria-expanded')).toBe('false');
  });

  it('"Open the changed files" opens the panel on Files', async () => {
    withRun({ durableRun: null, isStreaming: false, lastDurableRun: RUN });
    api.events = [{ event: 'snapshot', data: view({ status: 'completed', files: [{ path: 'a.py', change: 'added' }] }) }];
    render(<Harness />);
    act(() => useActivityPanelStore.getState().openSection('files', false));
    const filesHeader = await screen.findByRole('button', { name: /Files changed/ });
    await waitFor(() => expect(document.activeElement).toBe(filesHeader));
    expect(useActivityPanelStore.getState().focusSection).toBeNull();
  });
});
