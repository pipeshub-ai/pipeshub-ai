import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AgentActivityTimeline, toolActivityLabel } from '../agent-activity';
import type { MessagePart } from '../../../types';

// No global RTL setup file is wired into vitest.config.ts for this
// project — clean up the DOM after each test ourselves so renders from
// one test don't leak into the next test's `screen` queries.
afterEach(() => cleanup());

// No JSX here: vitest's default esbuild transform isn't configured with a
// React JSX plugin (tsconfig.json's `jsx: "preserve"` assumes Next.js's own
// build handles it) — `createElement` renders through the same React DOM
// tree without needing a new toolchain dependency for this test file.
const h = React.createElement;

function renderTimeline(parts: MessagePart[], opts: { isNested?: boolean; isStreaming?: boolean } = {}) {
  return render(
    h(Theme, null, h(AgentActivityTimeline, { parts, isNested: opts.isNested, isStreaming: opts.isStreaming })),
  );
}

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

  it('hides the trailing open text part while streaming even without isFinal', () => {
    const { container } = renderTimeline(
      [{ type: 'text', content: 'Still streaming this preamble...' }],
      { isStreaming: true },
    );

    expect(container.textContent).not.toContain('Still streaming this preamble...');
  });

  it('shows a non-trailing narration part while streaming once a later part exists', () => {
    renderTimeline(
      [
        { type: 'text', content: 'Let me check the test file first.' },
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'run_tests', status: 'running' },
      ],
      { isStreaming: true },
    );

    expect(screen.getByText('Let me check the test file first.')).toBeTruthy();
  });

  it('renders every text part (including isFinal) when nested inside a sub-agent group', () => {
    renderTimeline(
      [{ type: 'text', content: 'Delegate final answer.', isFinal: true }],
      { isNested: true },
    );

    expect(screen.getByText('Delegate final answer.')).toBeTruthy();
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
    renderTimeline([{ type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' }]);

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
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'jira.search_issues', status: 'completed' },
    ]);

    fireEvent.click(screen.getByText('Explored 2 searches'));

    expect(screen.getByText('Searched the web')).toBeTruthy();
  });

  it('labels a non-search multi-tool burst as "Ran N tools"', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'run_code', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'install_packages', status: 'completed' },
    ]);

    expect(screen.getByText('Ran 2 tools')).toBeTruthy();
  });

  it('does not group tool calls separated by a text/reasoning part', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
      { type: 'text', content: 'Found something interesting.' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'fetch_url', status: 'completed' },
    ]);

    expect(screen.queryByText(/Explored \d+ searches/)).toBeNull();
    expect(screen.getByText('Searched the web')).toBeTruthy();
    expect(screen.getByText('Read a web page')).toBeTruthy();
  });
});

describe('toolActivityLabel — derived, model-independent labels', () => {
  it('maps known tool names to a human verb phrase', () => {
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'web_search' })).toBe('Searched the web');
    // Wire tool names are `{app}__{tool}` (double underscore, see
    // `get_tool_name()` in `agent_loop_lib/tools/decorators.py`), not
    // dot-separated — the label map must be keyed on the real wire format.
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'retrieval__search_internal_knowledge' })).toBe(
      'Searched the knowledge base',
    );
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'run_code' })).toBe('Ran code');
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
