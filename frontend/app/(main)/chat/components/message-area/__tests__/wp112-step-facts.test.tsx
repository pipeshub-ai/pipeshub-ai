/**
 * WP-112 ACTDETAIL: an engine's tool step says which page it read, what it searched for, which pattern or file,
 * and what came of it; its result is behind "Show result" and its raw arguments behind "Details".
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { ToolCallCard } from '../agent-activity';
import { citedNumbers } from '../tool-step';
import { engineSteps, stepFacts, withEngineSteps } from '../../../engine-steps';
import type { MessagePart, SSEEngineProgressEvent } from '../../../types';
import type { CitationMaps } from '../response-tabs/citations';

afterEach(() => cleanup());

const h = React.createElement;

const frame = (kind: string, text: string, data: Record<string, unknown>): SSEEngineProgressEvent =>
  ({ tool: 'code_task', taskId: 'run_1', kind, text, data }) as SSEEngineProgressEvent;

/** A research run's first page read, as the fixed worker writes it: the start, the gateway's result, the end. */
const READ: SSEEngineProgressEvent[] = [
  frame('tool', 'dynamic__fetch_url', {
    callId: 'call_a',
    args: '{"url": "https://onyx.app"}',
    step: { action: 'readPage', target: 'onyx.app', href: 'https://onyx.app' },
  }),
  frame('tool_result', 'dynamic__fetch_url', {
    callId: 'call_a',
    status: 'completed',
    result: 'Blocks of content from the URL:\nurl: https://onyx.app\ncontent: Complete Context',
    invocationId: 'inv_3',
    argsSummary: 'Reading https://onyx.app',
    resultSummary: 'Read “Onyx | Complete context” (onyx.app)',
    step: { action: 'readPage', target: 'onyx.app', href: 'https://onyx.app', title: 'Onyx | Complete context', sources: ['https://onyx.app'] },
  }),
  frame('tool_result', 'dynamic__fetch_url', { callId: 'call_a', status: 'completed' }),
];

function rows(frames: SSEEngineProgressEvent[]): MessagePart[] {
  return withEngineSteps([{ type: 'engine_task', runId: 'run_1', frames } as MessagePart], true);
}

function renderCard(part: MessagePart, citationMaps?: CitationMaps) {
  return render(h(Theme, null, h(ToolCallCard, { part, citationMaps })));
}

describe('engine steps fold their facts', () => {
  it('a page read keeps what it asked and gains what came back', () => {
    const [part] = rows(READ);
    expect(part.type).toBe('tool_call');
    expect(part.step).toEqual({
      action: 'readPage',
      target: 'onyx.app',
      href: 'https://onyx.app',
      title: 'Onyx | Complete context',
      sources: ['https://onyx.app'],
    });
    expect(part.argsSummary).toBe('Reading https://onyx.app');
    expect(part.resultSummary).toContain('Onyx');
    expect(part.args).toBe('{"url": "https://onyx.app"}');
  });

  it('late input (a tool_update) enriches the same step', () => {
    const steps = engineSteps(
      [
        frame('tool', 'WebFetch', { callId: 'c' }),
        frame('tool_update', 'WebFetch', { callId: 'c', step: { action: 'readPage', target: 'onyx.app', href: 'https://onyx.app' } }),
      ],
      false,
    );
    expect(steps).toHaveLength(1);
    expect(steps[0].kind === 'tool' && steps[0].step?.target).toBe('onyx.app');
  });

  it('refuses an unknown action and a link that is not http', () => {
    expect(stepFacts({ action: 'deleteAll', target: 'x' })).toBeUndefined();
    expect(stepFacts({ action: 'readPage', target: 'x', href: 'javascript:alert(1)' })).toEqual({ action: 'readPage', target: 'x' });
  });
});

describe('a step row names its target', () => {
  it('a web read shows the page title as a link, with no raw id or JSON on the row', () => {
    renderCard(rows(READ)[0]);
    expect(screen.getByText('Read a page')).toBeTruthy();
    const link = screen.getByRole('link', { name: 'Onyx | Complete context' });
    expect(link.getAttribute('href')).toBe('https://onyx.app');
    expect(screen.queryByText(/call_a|inv_3|dynamic__|\{"url"/)).toBeNull();
  });

  it('a search shows its query and its hits', () => {
    const [part] = rows([
      frame('tool', 'dynamic__web_search', { callId: 's', step: { action: 'webSearch', target: 'PipesHub Reddit' } }),
      frame('tool_result', 'dynamic__web_search', { callId: 's', status: 'completed', step: { action: 'webSearch', target: 'PipesHub Reddit', count: 0 } }),
    ]);
    renderCard(part);
    expect(screen.getByText('Searched the web')).toBeTruthy();
    expect(screen.getByText('“PipesHub Reddit”')).toBeTruthy();
    expect(screen.getByTestId('step-target').textContent).toContain('No results');
  });

  it("an engine's file search shows its pattern and how many files, never 'Glob'", () => {
    const [part] = rows([
      frame('tool', 'Glob', { callId: 'g', args: '{"pattern": "**/*.md"}', step: { action: 'findFiles', target: '**/*.md' } }),
      frame('tool_result', 'Glob', { callId: 'g', status: 'completed', result: 'a.md\nb.md', step: { action: 'findFiles', target: '**/*.md', count: 2 } }),
    ]);
    renderCard(part);
    expect(screen.getByText('Found files')).toBeTruthy();
    expect(screen.getByText('**/*.md')).toBeTruthy();
    expect(screen.getByTestId('step-target').textContent).toContain('2 files');
    expect(screen.queryByText('Glob')).toBeNull();
  });

  it('a file read shows its path', () => {
    const [part] = rows([frame('tool', 'Read', { callId: 'r', step: { action: 'readFile', target: 'notes/progress.md' } })]);
    renderCard(part);
    expect(screen.getByText('Read a file')).toBeTruthy();
    expect(screen.getByText('notes/progress.md')).toBeTruthy();
  });
});

describe('the result and the arguments are collapsed', () => {
  it('"Show result" opens the result, "Details" the raw arguments', () => {
    renderCard(rows(READ)[0]);
    fireEvent.click(screen.getByRole('button', { expanded: false, name: /Read a page/ }));
    expect(document.body.textContent).toContain('Reading https://onyx.app');
    expect(screen.queryByTestId('tool-result')).toBeNull();
    fireEvent.click(screen.getByText('Show result'));
    expect(screen.getByTestId('tool-result').textContent).toContain('Onyx | Complete context');
    expect(screen.queryByText(/"url"/)).toBeNull();
    fireEvent.click(screen.getByText('Details'));
    expect(screen.getByText(/"url"/)).toBeTruthy();
  });
});

describe("the answer's citations point at the step that produced them", () => {
  const maps = {
    citations: {
      c1: { citationId: 'c1', chunkIndex: 1, webUrl: 'https://onyx.app#:~:text=Complete' },
      c2: { citationId: 'c2', chunkIndex: 2, webUrl: 'https://pipeshub.com#:~:text=Secure' },
      c3: { citationId: 'c3', chunkIndex: 3, webUrl: 'https://onyx.app#:~:text=Faster' },
    },
    sources: {},
    sourcesOrder: [],
    citationsOrder: {},
  } as unknown as CitationMaps;

  it('names the [n] whose link the step returned', () => {
    const [part] = rows(READ);
    expect(citedNumbers(part.step!, maps)).toEqual([1, 3]);
    renderCard(part, maps);
    expect(screen.getByTestId('step-target').textContent).toContain('Cited as [1] [3]');
  });
});
