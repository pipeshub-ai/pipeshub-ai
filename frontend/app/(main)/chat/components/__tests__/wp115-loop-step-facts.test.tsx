/**
 * WP-115 (WP112-FU-1, FU-2): the built-in agent's own steps read as an engine's ("Read a page · onyx.app"), live and
 * after a reload, and the Activity panel's rows read the same facts: the same label, target, link and count.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { SSEEvent } from '@/lib/api';
import type { ActivityTool, DurableAgentRunHandle, MessagePart } from '@/chat/types';
import { toActivityView } from '@/chat/activity';
import { createAGUIEventHandler } from '@/chat/agui-event-handler';
import type { StreamMessageCallbacks } from '@/chat/api';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', () => ({
  apiClient: api,
  streamSSEGet: vi.fn(),
}));

import { ActivityPanelView } from '../activity-panel/activity-panel';
import { partOfTool, toolOfPart } from '../activity-panel/activity-steps';
import { forgetStepDetails } from '../activity-panel/use-step-detail';
import { ToolCallCard } from '../message-area/agent-activity';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };
const PAGE = 'https://www.onyx.app/pricing';
const ASKED = { action: 'readPage', target: 'onyx.app', href: PAGE };
const READ = { ...ASKED, title: 'Onyx pricing', sources: [PAGE] };

beforeEach(() => {
  api.get.mockReset();
  forgetStepDetails();
});
afterEach(cleanup);

function frame(type: string, fields: Record<string, unknown> = {}): SSEEvent {
  return { event: type, data: { type, runId: 'root', ...fields } };
}

/** The loop's call as Node's live frames carry it, and the part they build. */
function livePart(asked: unknown, answered: unknown): MessagePart {
  const onParts = vi.fn();
  const handle = createAGUIEventHandler({ onParts } as unknown as StreamMessageCallbacks);
  handle(frame('TOOL_CALL_START', { toolCallId: 'c1', toolCallName: 'dynamic__fetch_url' }));
  handle(frame('TOOL_CALL_ARGS', { toolCallId: 'c1', delta: JSON.stringify({ url: PAGE }), step: asked }));
  handle(frame('TOOL_CALL_RESULT', { toolCallId: 'c1', content: 'Onyx pricing ...', status: 'completed', step: answered }));
  const parts = onParts.mock.calls.at(-1)?.[0] as MessagePart[];
  return parts.find((p) => p.toolCallId === 'c1')!;
}

function panel(tools: unknown[]) {
  const view = toActivityView({
    runId: RUN.runId,
    status: 'completed',
    engine: false,
    commands: [],
    omittedCommands: 0,
    tools,
    tasks: [],
    files: [],
    followups: [],
  });
  expect(view).not.toBeNull();
  return render(
    <Theme>
      <ActivityPanelView run={RUN} state={{ view: view!, connection: 'live' }} variant="pane" onClose={vi.fn()} />
    </Theme>,
  );
}

describe('WP-115: a loop step names its page live, as after a reload', () => {
  it('LS-F1: the live part keeps the facts the result brought back', () => {
    expect(livePart(ASKED, READ).step).toEqual(READ);
  });

  it('LS-F2: facts outside the contract (a script link, an unknown action) are dropped', () => {
    const part = livePart({ action: 'readPage', href: 'javascript:alert(1)', target: 'x' }, { action: 'runCommand' });
    expect(part.step).toEqual({ action: 'readPage', target: 'x' });
  });

  it('LS-F3: the chat row reads "Read a page · Onyx pricing", linked, instead of the tool name', () => {
    render(
      <Theme>
        <ToolCallCard part={livePart(ASKED, READ)} />
      </Theme>,
    );
    expect(screen.getByText('Read a page')).toBeTruthy();
    const link = within(screen.getByTestId('step-target')).getByRole('link');
    expect(link.textContent).toBe('Onyx pricing');
    expect(link.getAttribute('href')).toBe(PAGE);
    expect(document.body.textContent).not.toContain('dynamic__fetch_url');
  });
});

const tool = (over: Partial<ActivityTool> & { toolCallId: string; name: string }): ActivityTool => ({ status: 'completed', ...over });

describe('WP-115: the Activity panel reads a step\'s facts', () => {
  it('LS-F4: a page read names the page; its link opens from the step\'s body, never the row\'s button', () => {
    panel([tool({ toolCallId: 'c1', name: 'dynamic__fetch_url', argsSummary: `Reading ${PAGE}`, step: READ as ActivityTool['step'] })]);
    const row = screen.getByTestId('activity-tool');
    expect(within(row).getByTestId('activity-tool-name').textContent).toBe('Read a page');
    expect(within(row).getByTestId('activity-tool-subject').textContent).toBe(' · Onyx pricing');
    expect(within(row).getByTestId('activity-tool-toggle').querySelector('a')).toBeNull();
    fireEvent.click(within(row).getByTestId('activity-tool-toggle'));
    const link = within(row).getByTestId('activity-tool-link');
    expect(link.getAttribute('href')).toBe(PAGE);
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('LS-F5: a search names its query and its count is the outcome line', () => {
    panel([
      tool({ toolCallId: 's1', name: 'dynamic__web_search', resultSummary: 'Web search finished', step: { action: 'webSearch', target: 'PipesHub Reddit', count: 5 } }),
      tool({ toolCallId: 's2', name: 'google_web_search', step: { action: 'webSearch', target: 'onyx', count: 0 } }),
    ]);
    const [first, second] = screen.getAllByTestId('activity-tool');
    expect(within(first!).getByTestId('activity-tool-subject').textContent).toBe(' · “PipesHub Reddit”');
    expect(within(first!).getByTestId('activity-tool-outcome').textContent).toContain('5 results');
    expect(within(second!).getByTestId('activity-tool-name').textContent).toBe('Searched the web');
    expect(within(second!).getByTestId('activity-tool-outcome').textContent).toContain('No results');
  });

  it('LS-F6: the snapshot\'s facts are checked and their words masked as any the panel shows', () => {
    const view = toActivityView({
      runId: RUN.runId, status: 'completed', engine: false, commands: [], omittedCommands: 0, tasks: [], files: [], followups: [],
      tools: [
        { toolCallId: 'a', name: 'x', status: 'completed', step: { action: 'readPage', href: 'javascript:alert(1)', target: 'evil' } },
        { toolCallId: 'b', name: 'x', status: 'completed', step: { action: 'deleteAll' } },
      ],
    })!;
    expect(view.tools[0]!.step).toEqual({ action: 'readPage', target: 'evil' });
    expect(view.tools[1]!.step).toBeUndefined();
  });

  it('LS-F7: a child run\'s step (an engine\'s) and the panel\'s own step carry the same facts both ways', () => {
    const part: MessagePart = { type: 'tool_call', toolCallId: 'e1', toolName: 'WebFetch', status: 'completed', step: READ as MessagePart['step'] };
    const back = toolOfPart(part)!;
    expect(back.step).toEqual(READ);
    expect(partOfTool(back).step).toEqual(READ);
  });
});
