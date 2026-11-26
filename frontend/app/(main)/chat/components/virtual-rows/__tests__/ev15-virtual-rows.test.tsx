/**
 * EV-15 VR-4..VR-11 and PF-17 AP-1..AP-2: a 10,000-event run in the chat's timeline and in the activity panel
 * keeps a bounded DOM, shows the rows at the scroll position, keeps a row's opened state across a scroll away and
 * back, and holds the reader's place when a row above them changes height. jsdom has no layout, so the scroll
 * container's box and each row's height are given here, as a browser would report them.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ActivityCommand, ActivityTool, ActivityView, DurableAgentRunHandle, MessagePart } from '@/chat/types';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(() => new Promise(() => {})), post: vi.fn() },
  streamSSEGet: () => new Promise(() => {}),
}));

import { VirtualRows, VIRTUALIZE_AFTER, useRowState } from '../virtual-rows';
import { AgentActivityTimeline } from '../../message-area/agent-activity';
import { ActivityPanelView } from '../../activity-panel/activity-panel';

const h = React.createElement;
const VIEW_HEIGHT = 600;
/** Rows a 600 px view with 800 px overscan each side can hold at the smallest row height used here (20 px). */
const MAX_ROWS = Math.ceil((VIEW_HEIGHT + 2 * 800) / 20) + 2;

/** Every observed element, and a way to report sizes to the observers as a browser does after layout. */
class FakeResizeObserver {
  static all = new Set<FakeResizeObserver>();
  readonly targets = new Set<Element>();
  constructor(private readonly callback: ResizeObserverCallback) {
    FakeResizeObserver.all.add(this);
  }
  observe(target: Element) {
    this.targets.add(target);
  }
  unobserve(target: Element) {
    this.targets.delete(target);
  }
  disconnect() {
    this.targets.clear();
    FakeResizeObserver.all.delete(this);
  }
  static flush() {
    for (const observer of [...FakeResizeObserver.all]) {
      const entries = [...observer.targets].map((target) => ({ target }) as unknown as ResizeObserverEntry);
      if (entries.length) observer.callback(entries, observer as unknown as ResizeObserver);
    }
  }
}

/** The scroll container's box: its top at 0 and its height; a list's top is where it sits minus the scroll. */
let rowHeight: (row: HTMLElement) => number = () => 20;
function installLayout() {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.dataset.testid === 'scroller' ? VIEW_HEIGHT : 0;
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const scroller = this.closest('[data-testid="scroller"]') as HTMLElement | null;
    const scrollTop = scroller?.scrollTop ?? 0;
    const height = this.dataset.vrow !== undefined ? rowHeight(this) : 0;
    // Every list here starts at the top of the scroll container's content.
    const top = this.dataset.testid === 'scroller' ? 0 : -scrollTop;
    return { top, bottom: top + height, height, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
  });
}

async function nextFrame() {
  await act(async () => {
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
  });
}

async function scrollTo(scroller: HTMLElement, top: number) {
  scroller.scrollTop = top;
  fireEvent.scroll(scroller);
  await nextFrame();
}

function Scroller({ children }: { children: React.ReactNode }) {
  return h('div', { 'data-testid': 'scroller', style: { overflowY: 'auto', height: VIEW_HEIGHT } }, children);
}

beforeEach(() => {
  FakeResizeObserver.all.clear();
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  rowHeight = () => 20;
  installLayout();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function Row({ label }: { label: string }) {
  const [open, setOpen] = useRowState('open', false);
  return h('button', { type: 'button', 'aria-expanded': open, onClick: () => setOpen((v) => !v) }, label);
}

function renderList(count: number) {
  const items = Array.from({ length: count }, (_, i) => `row ${i}`);
  return render(
    h(Scroller, null, h(VirtualRows<string>, {
      items,
      getKey: (item) => item,
      estimateSize: () => 20,
      renderRow: (item) => h(Row, { label: item }),
      'data-testid': 'list',
    })),
  );
}

describe('EV-15 VirtualRows', () => {
  it('VR-4: a list at the threshold renders every row as before, with no virtualization', () => {
    renderList(VIRTUALIZE_AFTER);
    expect(screen.getAllByRole('button')).toHaveLength(VIRTUALIZE_AFTER);
    expect(screen.getByTestId('list').dataset.virtualized).toBeUndefined();
  });

  it('VR-5: 10,000 rows keep a bounded DOM and the padding stands in for the rest', () => {
    renderList(10_000);
    const list = screen.getByTestId('list');
    expect(list.dataset.virtualized).toBe('true');
    const rows = list.querySelectorAll('[data-vrow]');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThanOrEqual(MAX_ROWS);
    expect(screen.getByText('row 0')).toBeTruthy();
    expect(screen.queryByText('row 9999')).toBeNull();
    const [top, , bottom] = [list.firstElementChild as HTMLElement, null, list.lastElementChild as HTMLElement];
    expect(top.style.height).toBe('0px');
    expect(parseFloat(bottom.style.height)).toBe((10_000 - rows.length) * 20);
  });

  it('VR-6: scrolling shows the rows at the new position, and only those', async () => {
    renderList(10_000);
    const scroller = screen.getByTestId('scroller');
    await scrollTo(scroller, 100_000);
    // Row 5,000 starts at 100,000 px.
    expect(screen.getByText('row 5000')).toBeTruthy();
    expect(screen.getByText('row 5029')).toBeTruthy();
    expect(screen.queryByText('row 0')).toBeNull();
    expect(screen.getByTestId('list').querySelectorAll('[data-vrow]').length).toBeLessThanOrEqual(MAX_ROWS);
    await scrollTo(scroller, 199_400);
    expect(screen.getByText('row 9999')).toBeTruthy();
  });

  it('VR-7: a row the person opened is still open after it scrolled away and back', async () => {
    renderList(10_000);
    const scroller = screen.getByTestId('scroller');
    fireEvent.click(screen.getByText('row 3'));
    expect(screen.getByText('row 3').getAttribute('aria-expanded')).toBe('true');
    await scrollTo(scroller, 100_000);
    expect(screen.queryByText('row 3')).toBeNull();
    await scrollTo(scroller, 0);
    expect(screen.getByText('row 3').getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('row 4').getAttribute('aria-expanded')).toBe('false');
  });

  it('VR-8: rows measured taller than estimated move the padding, and one above the view keeps the reader’s place', async () => {
    renderList(10_000);
    const scroller = screen.getByTestId('scroller');
    await scrollTo(scroller, 100_000);
    // Every rendered row turns out 50 px: the ones above the view's top shift what the reader sees by as much.
    rowHeight = () => 50;
    const above = [...screen.getByTestId('list').querySelectorAll<HTMLElement>('[data-vrow]')].filter(
      (row) => Number(row.dataset.vrow?.split(' ')[1]) < 5_000,
    ).length;
    expect(above).toBeGreaterThan(0);
    await act(async () => FakeResizeObserver.flush());
    expect(scroller.scrollTop).toBe(100_000 + above * 30);
  });

  it('VR-9: two rows with the same key are measured and opened apart', () => {
    render(
      h(Scroller, null, h(VirtualRows<string>, {
        items: ['same', 'same'],
        getKey: (item) => item,
        estimateSize: () => 20,
        renderRow: (_, index) => h(Row, { label: `copy ${index}` }),
      })),
    );
    fireEvent.click(screen.getByText('copy 0'));
    expect(screen.getByText('copy 1').getAttribute('aria-expanded')).toBe('false');
  });
});

const tool = (i: number): MessagePart => ({
  type: 'tool_call',
  toolCallId: `call-${i}`,
  toolName: i % 2 ? 'knowledgegraph__fetch_record' : 'jira__search_issues',
  status: 'completed',
});
const narration = (i: number): MessagePart => ({ type: 'text', content: `Step ${i} done.`, settled: true });

describe('EV-15 the chat timeline of a 10,000-event run', () => {
  it('VR-10: alternating narration and tool calls render a bounded number of timeline rows', () => {
    const parts = Array.from({ length: 10_000 }, (_, i) => (i % 2 ? tool(i) : narration(i)));
    render(h(Theme, null, h(Scroller, null, h(AgentActivityTimeline, { parts }))));
    const rows = screen.getByTestId('agent-activity-rows');
    expect(rows.dataset.virtualized).toBe('true');
    expect(rows.querySelectorAll('[data-vrow]').length).toBeLessThanOrEqual(MAX_ROWS);
    expect(screen.getByText('Step 0 done.')).toBeTruthy();
    expect(screen.queryByText('Step 9998 done.')).toBeNull();
  });

  it('VR-11: a group of 10,000 consecutive calls, opened, renders a bounded number of cards', () => {
    const parts = Array.from({ length: 10_000 }, (_, i) => tool(i));
    render(h(Theme, null, h(Scroller, null, h(AgentActivityTimeline, { parts }))));
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    const group = document.querySelector('[data-virtualized="true"]');
    expect(group).not.toBeNull();
    expect(group!.querySelectorAll('[data-vrow]').length).toBeLessThanOrEqual(MAX_ROWS);
  });
});

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };
const panelTool = (i: number): ActivityTool => ({
  toolCallId: `t-${i}`,
  name: i % 3 ? 'jira__search_issues' : 'knowledgehub__search',
  status: 'completed',
} as ActivityTool);
/** 256 KiB of output per command: the panel's own bound (`ACTIVITY_TERMINAL_BOUNDS`). */
const OUTPUT = Array.from({ length: 4_096 }, (_, i) => `line ${i} ${'x'.repeat(56)}`).join('\n');
const panelCommand = (i: number): ActivityCommand => ({
  commandId: `cmd-${i}`,
  source: 'run_code',
  command: `pytest -q tests/test_${i}.py`,
  status: 'completed',
  exitCode: 0,
  startedAt: 1_760_000_000_000,
  durationMs: 1_000,
  stdout: OUTPUT,
  stderr: '',
  truncated: true,
  liveDropped: 0,
  hasLog: false,
} as ActivityCommand);

function bigView(): ActivityView {
  return {
    runId: RUN.runId,
    status: 'running',
    engine: true,
    commands: Array.from({ length: 200 }, (_, i) => panelCommand(i)),
    omittedCommands: 0,
    tools: Array.from({ length: 10_000 }, (_, i) => panelTool(i)),
    tasks: [],
    files: [],
    followups: [],
  } as unknown as ActivityView;
}

describe('PF-17 the activity panel with 10,000 steps and 200 full terminals', () => {
  it('AP-1: the panel keeps a bounded DOM: few command blocks, few steps, windowed terminals', () => {
    render(
      h(Theme, null, h('div', { style: { height: VIEW_HEIGHT } },
        h(ActivityPanelView, { run: RUN, state: { view: bigView(), connection: 'live' }, variant: 'pane', onClose: () => {} }))),
    );
    const commands = screen.getAllByTestId('activity-command');
    expect(commands.length).toBeLessThan(20);
    const steps = screen.getByTestId('activity-steps');
    expect(steps.dataset.virtualized).toBe('true');
    expect(steps.querySelectorAll('[data-vrow]').length).toBeLessThan(200);
    // Terminals were already windowed (FE-5): 51 MB of output across the run stays a few thousand nodes.
    expect(document.querySelectorAll('[data-stream]').length).toBeLessThan(2_000);
  });

  it('AP-2: while following, the panel stays at its end as virtualized rows are measured', async () => {
    render(
      h(Theme, null, h('div', { style: { height: VIEW_HEIGHT } },
        h(ActivityPanelView, { run: RUN, state: { view: bigView(), connection: 'live' }, variant: 'pane', onClose: () => {} }))),
    );
    const body = screen.getByTestId('activity-panel').querySelector<HTMLElement>('[style*="overflow-y: auto"]')!;
    let height = 10_000;
    vi.spyOn(body, 'scrollHeight', 'get').mockImplementation(() => height);
    height = 640_000;
    await act(async () => FakeResizeObserver.flush());
    expect(body.scrollTop).toBe(640_000);
  });
});
