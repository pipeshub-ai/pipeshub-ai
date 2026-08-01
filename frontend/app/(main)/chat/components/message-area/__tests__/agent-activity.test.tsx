import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import {
  AgentActivityTimeline,
  toolActivityLabel,
  hasMultiStepActivity,
  getVisibleRootParts,
  buildActivitySummary,
  CollapsibleActivitySection,
} from '../agent-activity';
import type { MessagePart, StatusMessage } from '../../../types';

// No global RTL setup file is wired into vitest.config.ts for this
// project — clean up the DOM after each test ourselves so renders from
// one test don't leak into the next test's `screen` queries.
afterEach(() => cleanup());

// No JSX here: vitest's default esbuild transform isn't configured with a
// React JSX plugin (tsconfig.json's `jsx: "preserve"` assumes Next.js's own
// build handles it) — `createElement` renders through the same React DOM
// tree without needing a new toolchain dependency for this test file.
const h = React.createElement;

function renderTimeline(
  parts: MessagePart[],
  opts: { isNested?: boolean; isStreaming?: boolean; currentStatus?: StatusMessage | null } = {},
) {
  return render(
    h(Theme, null, h(AgentActivityTimeline, {
      parts,
      isNested: opts.isNested,
      isStreaming: opts.isStreaming,
      currentStatus: opts.currentStatus,
    })),
  );
}

const STATUS: StatusMessage = { id: 's1', status: 'executing', message: 'Using Jira Search...', timestamp: '' };

describe('AgentActivityTimeline — narration text', () => {
  it('renders a root-level narration text part', () => {
    renderTimeline([{ type: 'text', content: 'Let me check the test file first.' }]);

    expect(screen.getByText('Let me check the test file first.')).toBeTruthy();
  });

  it('hides the root-level text part marked isFinal', () => {
    const { container } = renderTimeline([
      { type: 'text', content: 'Let me check the test file first.' },
      { type: 'text', content: 'Final answer text.', isFinal: true },
    ]);

    expect(screen.getByText('Let me check the test file first.')).toBeTruthy();
    expect(container.textContent).not.toContain('Final answer text.');
  });

  it('hides the trailing open text part while streaming a SIMPLE response (no tools/reasoning yet)', () => {
    // No other activity exists yet, so this could still just be a
    // single-shot answer — leave it mirrored in `streamingContent` /
    // `AnswerContent` only, don't duplicate it here.
    const { container } = renderTimeline(
      [{ type: 'text', content: 'Still streaming this preamble...' }],
      { isStreaming: true },
    );

    expect(container.textContent).not.toContain('Still streaming this preamble...');
  });

  it('shows a settled narration part while streaming even if a later part exists', () => {
    renderTimeline(
      [
        { type: 'text', content: 'Let me check the test file first.', settled: true },
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'run_tests', status: 'running' },
      ],
      { isStreaming: true },
    );

    expect(screen.getByText('Let me check the test file first.')).toBeTruthy();
  });

  it('shows a not-yet-settled trailing text part LIVE once a reasoning/tool_call part proves this is a multi-step response', () => {
    // Once there's other activity (a reasoning block here), `AnswerContent`
    // is suppressed for the whole turn (see `ChatResponse`) — so the still-
    // unsettled text has nowhere else to render and must appear live, in
    // the timeline itself, with a typing-cursor treatment (`LiveNarrationText`)
    // rather than the plain `NarrationText` used for already-settled text.
    const { container } = renderTimeline(
      [
        { type: 'text', content: 'Still working on this.' },
        { type: 'reasoning', content: 'thinking...' },
      ],
      { isStreaming: true },
    );

    expect(screen.getByText('Still working on this.')).toBeTruthy();
    expect(container.querySelector('.live-narration-text')).not.toBeNull();
    expect(container.querySelector('.typing-cursor')).not.toBeNull();
  });

  it('shows a not-yet-settled trailing text part LIVE once a tool_call part proves this is a multi-step response', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: 'Found something, let me dig deeper.' },
      ],
      { isStreaming: true },
    );

    expect(screen.getByText('Found something, let me dig deeper.')).toBeTruthy();
    expect(container.querySelector('.live-narration-text')).not.toBeNull();
  });

  it('renders nothing for an empty-content live text part (status indicator handles the signal)', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: '' },
      ],
      { isStreaming: true },
    );

    // Empty live text returns null — StatusTimelineEntry signals progress instead.
    expect(container.querySelector('.live-narration-text')).toBeNull();
    expect(container.querySelector('.typing-cursor')).toBeNull();
  });

  it('renders a settled narration part as plain (non-live) text even in a multi-step stream, while the trailing unsettled one stays live', () => {
    const { container } = renderTimeline(
      [
        { type: 'text', content: 'Already settled narration.', settled: true },
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: 'Currently being written.' },
      ],
      { isStreaming: true },
    );

    expect(screen.getByText('Already settled narration.')).toBeTruthy();
    expect(screen.getByText('Currently being written.')).toBeTruthy();
    // Only the trailing, unsettled part gets the live treatment.
    expect(container.querySelectorAll('.live-narration-text').length).toBe(1);
  });

  it('renders every text part (including isFinal) when nested inside a sub-agent group', () => {
    renderTimeline(
      [{ type: 'text', content: 'Delegate final answer.', isFinal: true }],
      { isNested: true },
    );

    expect(screen.getByText('Delegate final answer.')).toBeTruthy();
  });

  it('strips an artifact marker out of narration instead of rendering it raw', () => {
    const { container } = renderTimeline([
      {
        type: 'text',
        content:
          'Updated the image.\n::artifact[sachin.png](record:r-1){image/png|d-1|r-1|IMAGE|2}',
      },
    ]);

    expect(screen.getByText('Updated the image.')).toBeTruthy();
    expect(container.textContent).not.toContain('::artifact');
    // The marker's `record:` URL must never become an anchor here — the
    // browser cannot follow that scheme, so it renders as a dead link.
    expect(container.querySelector('a')).toBeNull();
  });

  it('renders nothing when a narration part is only a marker', () => {
    const { container } = renderTimeline([
      { type: 'text', content: '::download_conversation_task[report.csv](https://x/y)' },
    ]);

    expect(container.textContent).toBe('');
  });

  it('renders nothing when every part is filtered out', () => {
    // renderTimeline wraps in <Theme>, which always renders its own
    // wrapper div — assert AgentActivityTimeline itself contributed no
    // content rather than asserting the outer container is empty.
    const { container } = renderTimeline([{ type: 'text', content: 'draft', isFinal: true }]);
    expect(container.querySelector('.rt-Box')).toBeNull();
    expect(container.textContent).toBe('');
  });
});

describe('AgentActivityTimeline — tool call grouping', () => {
  it('renders a single tool call directly, without a group summary row', () => {
    renderTimeline([{ type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', displayName: 'Searched the web', status: 'completed' }]);

    expect(screen.getByText('Searched the web')).toBeTruthy();
    expect(screen.queryByText(/Explored \d+ searches/)).toBeNull();
  });

  it('collapses consecutive search-like tool calls into an "Explored N searches" summary', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'jira.search_issues', status: 'completed' },
    ]);

    expect(screen.getByText('Explored 2 searches')).toBeTruthy();
    // Individual cards are collapsed by default.
    expect(screen.queryByText('Searched the web')).toBeNull();
  });

  it('expands the group summary to reveal individual tool-call cards', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', displayName: 'Searched the web', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'jira.search_issues', status: 'completed' },
    ]);

    fireEvent.click(screen.getByText('Explored 2 searches'));

    expect(screen.getByText('Searched the web')).toBeTruthy();
  });

  it('labels a non-search multi-tool burst as "Ran N tools"', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'run_code', displayName: 'Ran code', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'install_packages', displayName: 'Installed packages', status: 'completed' },
    ]);

    expect(screen.getByText('Ran 2 tools')).toBeTruthy();
  });

  it('does not group tool calls separated by a text/reasoning part', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', displayName: 'Searched the web', status: 'completed' },
      { type: 'text', content: 'Found something interesting.' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'web_scrape', displayName: 'Read a web page', status: 'completed' },
    ]);

    expect(screen.queryByText(/Explored \d+ searches/)).toBeNull();
    expect(screen.getByText('Searched the web')).toBeTruthy();
    expect(screen.getByText('Read a web page')).toBeTruthy();
  });
});

describe('toolActivityLabel — derived, model-independent labels', () => {
  it('uses backend-provided displayName when present', () => {
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'web_search', displayName: 'Searched the web' })).toBe(
      'Searched the web',
    );
    expect(
      toolActivityLabel({
        type: 'tool_call',
        toolName: 'retrieval__search_internal_knowledge',
        displayName: 'Searched the knowledge base',
      }),
    ).toBe('Searched the knowledge base');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'run_code', displayName: 'Ran code' })).toBe('Ran code');
  });

  it('humanizes the tool name when displayName is absent', () => {
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'web_search' })).toBe('Web Search');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'retrieval__search_internal_knowledge' })).toBe(
      'Search Internal Knowledge',
    );
  });

  it('never renders blank for an unknown tool name — falls back to a humanized version', () => {
    const label = toolActivityLabel({ type: 'tool_call', toolName: 'custom_connector_do_thing' });
    expect(label.length).toBeGreaterThan(0);
    expect(label).toContain('Custom');
  });

  it('falls back to a generic label when toolName is missing entirely', () => {
    expect(toolActivityLabel({ type: 'tool_call' })).toBe('Used a tool');
  });
});

describe('AgentActivityTimeline — status indicator as a timeline entry', () => {
  it('renders the current status as the last entry while streaming', () => {
    const { container } = renderTimeline(
      [{ type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' }],
      { isStreaming: true, currentStatus: STATUS },
    );

    // The shimmer treatment renders the message text twice (base + sweep
    // overlay layers) — assert via textContent rather than getByText.
    expect(container.textContent).toContain('Using Jira Search...');
  });

  it('renders the status entry alone when there are no other visible parts yet', () => {
    const { container } = renderTimeline([], { isStreaming: true, currentStatus: STATUS });

    expect(container.textContent).toContain('Using Jira Search...');
  });

  it('does not render a status entry once streaming has finished', () => {
    const { container } = renderTimeline(
      [{ type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' }],
      { isStreaming: false, currentStatus: STATUS },
    );

    expect(container.textContent).not.toContain('Using Jira Search...');
  });

  it('does not render a status entry for a nested (sub-agent) timeline', () => {
    const { container } = renderTimeline(
      [{ type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' }],
      { isNested: true, isStreaming: true, currentStatus: STATUS },
    );

    expect(container.textContent).not.toContain('Using Jira Search...');
  });

  it('renders nothing at all when there are no parts and no status', () => {
    const { container } = renderTimeline([], { isStreaming: true, currentStatus: null });
    expect(container.querySelector('.rt-Box')).toBeNull();
  });

  it('hides the status entry when there is an active live text part with content', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: 'Let me analyze this for you.' },
      ],
      { isStreaming: true, currentStatus: STATUS },
    );

    // Live text with content is rendering — status is suppressed.
    expect(container.querySelector('.live-narration-text')).not.toBeNull();
    expect(container.textContent).not.toContain('Using Jira Search...');
  });

  it('shows the status entry when the trailing text part has empty content', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: '' },
      ],
      { isStreaming: true, currentStatus: STATUS },
    );

    // Empty live text returns null so status is visible.
    expect(container.querySelector('.live-narration-text')).toBeNull();
    expect(container.textContent).toContain('Using Jira Search...');
  });
});

describe('hasMultiStepActivity', () => {
  it('is false for an empty or undefined transcript', () => {
    expect(hasMultiStepActivity([])).toBe(false);
    expect(hasMultiStepActivity(undefined)).toBe(false);
  });

  it('is false when the transcript only contains text parts', () => {
    expect(hasMultiStepActivity([{ type: 'text', content: 'hello' }])).toBe(false);
  });

  it('is true when a tool_call, reasoning, or sub_agent part is present', () => {
    expect(hasMultiStepActivity([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }])).toBe(true);
    expect(hasMultiStepActivity([{ type: 'reasoning', content: 'thinking' }])).toBe(true);
    expect(hasMultiStepActivity([{ type: 'sub_agent', runId: 'r1', parts: [] }])).toBe(true);
  });
});

describe('getVisibleRootParts', () => {
  it('always hides the isFinal part, streaming or not', () => {
    const parts: MessagePart[] = [{ type: 'text', content: 'the answer', isFinal: true }];
    expect(getVisibleRootParts(parts, true)).toEqual([]);
    expect(getVisibleRootParts(parts, false)).toEqual([]);
  });

  it('hides unsettled trailing text while streaming a simple (single-shot) response', () => {
    const parts: MessagePart[] = [{ type: 'text', content: 'draft answer' }];
    expect(getVisibleRootParts(parts, true)).toEqual([]);
  });

  it('shows unsettled trailing text while streaming a multi-step response', () => {
    const parts: MessagePart[] = [
      { type: 'tool_call', toolCallId: 'c1', toolName: 'search' },
      { type: 'text', content: 'candidate text' },
    ];
    expect(getVisibleRootParts(parts, true)).toEqual(parts);
  });

  it('shows settled narration regardless of streaming state', () => {
    const parts: MessagePart[] = [{ type: 'text', content: 'narration', settled: true }];
    expect(getVisibleRootParts(parts, true)).toEqual(parts);
    expect(getVisibleRootParts(parts, false)).toEqual(parts);
  });
});

describe('buildActivitySummary', () => {
  it('describes a single tool call', () => {
    expect(buildActivitySummary([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }])).toBe('Used 1 tool');
  });

  it('describes multiple tool calls', () => {
    expect(
      buildActivitySummary([
        { type: 'tool_call', toolCallId: 'c1', toolName: 't' },
        { type: 'tool_call', toolCallId: 'c2', toolName: 't' },
      ]),
    ).toBe('Used 2 tools');
  });

  it('combines reasoning and tool segments', () => {
    expect(
      buildActivitySummary([
        { type: 'reasoning', content: 'thinking' },
        { type: 'tool_call', toolCallId: 'c1', toolName: 't' },
      ]),
    ).toBe('Thought about this · Used 1 tool');
  });

  it('describes sub-agent delegation', () => {
    expect(buildActivitySummary([{ type: 'sub_agent', runId: 'r1', parts: [] }])).toBe('Delegated to 1 sub-agent');
  });

  it('falls back to a generic label when there is no groupable activity', () => {
    expect(buildActivitySummary([{ type: 'text', content: 'narration', settled: true }])).toBe('Worked on this');
  });
});

describe('CollapsibleActivitySection', () => {
  function renderCollapsible(parts: MessagePart[]) {
    return render(
      h(Theme, null, h(CollapsibleActivitySection, { parts }, h('div', { 'data-testid': 'child' }, 'child content'))),
    );
  }

  it('hides its children (collapsed) by default', () => {
    renderCollapsible([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }]);
    expect(screen.queryByTestId('child')).toBeNull();
    expect(screen.getByText('Used 1 tool')).toBeTruthy();
  });

  it('shows its children after the summary header is clicked, and hides them again on a second click', () => {
    renderCollapsible([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }]);

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(screen.getByTestId('child')).toBeTruthy();

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(screen.queryByTestId('child')).toBeNull();
  });

  it('closes automatically the moment a live response finishes streaming, even if the user expanded it mid-stream', () => {
    // Mirrors chat-response.tsx's `isStreaming ? <AgentActivityTimeline /> : <CollapsibleActivitySection>...`
    // — the timeline renders bare (always "expanded") while streaming, then
    // gets wrapped in `CollapsibleActivitySection` the instant streaming ends.
    const parts: MessagePart[] = [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }];
    function Harness({ isStreaming }: { isStreaming: boolean }) {
      return isStreaming
        ? h('div', { 'data-testid': 'live-timeline' }, 'live')
        : h(CollapsibleActivitySection, { parts }, h('div', { 'data-testid': 'child' }, 'child content'));
    }

    const { rerender } = render(h(Theme, null, h(Harness, { isStreaming: true })));
    expect(screen.getByTestId('live-timeline')).toBeTruthy();

    rerender(h(Theme, null, h(Harness, { isStreaming: false })));
    // Freshly mounted CollapsibleActivitySection — closed by default, not
    // carrying over any expanded state from before the stream ended.
    expect(screen.queryByTestId('child')).toBeNull();
  });
});
