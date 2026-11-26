/**
 * TOOLNAMES (tool names in activity read as ids such as dynamic__web_search and
 * image_generator__generate_image): the timeline rows, the steps header and the activity drawer name every
 * tool in words, never by its id.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import testI18n from '@/lib/__tests__/test-i18n';
import type { DurableAgentRunHandle, MessagePart } from '@/chat/types';
import { toActivityView } from '@/chat/activity';
import { ActivityPanelView } from '../activity-panel/activity-panel';
import { AgentActivityTimeline, buildActivitySummary, countToolCalls } from '../message-area/agent-activity';

afterEach(cleanup);

const h = React.createElement;
const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };

/** A multi-tool turn as saved: built-ins without a display name, an MCP call with the worker's "Server · id". */
const CALLS: Array<Pick<MessagePart, 'toolName' | 'displayName'>> = [
  { toolName: 'dynamic__web_search' },
  { toolName: 'dynamic__fetch_url' },
  { toolName: 'image_generator__generate_image' },
  { toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket' },
  { toolName: 'jira__get_issue_by_id' },
  { toolName: 'pipeshub@sandbox/run_code' },
];

const RAW = /__|@|create_ticket|generate_image|web_search/;

function parts(): MessagePart[] {
  // A thought between calls keeps each its own row (consecutive calls fold into one group).
  return CALLS.flatMap((call, i): MessagePart[] => [
    { type: 'reasoning', content: `step ${i}` },
    { type: 'tool_call', toolCallId: `c${i}`, status: 'completed', ...call },
  ]);
}

describe('TOOLNAMES: friendly tool names', () => {
  it('the timeline rows', () => {
    const { container } = render(h(Theme, null, h(AgentActivityTimeline, { parts: parts() })));
    const text = container.textContent ?? '';
    for (const shown of ['Searched the web', 'Read a page', 'Image Generator', 'Generated an image', 'E2Esaas', 'Create ticket', 'Jira', 'Get issue by ID', 'Ran code']) {
      expect(text).toContain(shown);
    }
    expect(text).not.toMatch(RAW);
  });

  it('the steps header', () => {
    const summary = buildActivitySummary(parts(), testI18n.t);
    expect(summary).toContain('Searched the web');
    expect(summary).not.toMatch(RAW);
  });

  it('QA-FP4 6: the steps header names a batch in words, not "E2Esaas · create_ticket (3)"', () => {
    const batch: MessagePart[] = [0, 1, 2].map((i) => ({
      type: 'tool_call',
      toolCallId: `b${i}`,
      toolName: 'mcp_e2esaas_create_ticket',
      displayName: 'E2Esaas · create_ticket',
      status: 'completed',
    }));
    const summary = buildActivitySummary(batch, testI18n.t);
    expect(summary).toBe('Used E2Esaas: Create ticket (3)');
    expect(summary).not.toMatch(RAW);
  });

  it('QA-FP4 6: the header and a group row count the same calls, from one function', () => {
    // "Ran code · Used 1 tool" over a row "Ran 2 tools" was the finding: an unnamed call plus a named one.
    const burst: MessagePart[] = [
      { type: 'tool_call', toolCallId: 'a', toolName: 'search_tools', status: 'completed' },
      { type: 'tool_call', toolCallId: 'b', toolName: 'run_code', status: 'completed' },
      { type: 'tool_call', toolCallId: 'c', toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket', status: 'completed' },
      { type: 'tool_call', toolCallId: 'd', toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket', status: 'completed' },
    ];
    expect(countToolCalls(burst)).toBe(4);
    const { container } = render(h(Theme, null, h(AgentActivityTimeline, { parts: burst })));
    expect(container.textContent).toContain('Used 4 tools');
    const summary = buildActivitySummary(burst, testI18n.t);
    expect(summary).toBe('Ran code · Used E2Esaas: Create ticket (2) · Used 4 tools');
    // A header that names every call needs no total.
    expect(buildActivitySummary(burst.slice(1, 2), testI18n.t)).toBe('Ran code');
  });

  it('the activity drawer, the raw id kept off screen for debugging', () => {
    const view = toActivityView({
      runId: RUN.runId,
      status: 'completed',
      commands: [],
      omittedCommands: 0,
      tools: CALLS.map((call, i) => ({ toolCallId: `c${i}`, name: call.toolName, displayName: call.displayName, status: 'completed' })),
      tasks: [],
      files: [],
      followups: [],
    });
    expect(view).not.toBeNull();
    const { container } = render(
      h(Theme, null, h(ActivityPanelView, { run: RUN, state: { view: view!, connection: 'live' }, variant: 'pane', onClose: vi.fn() })),
    );
    const names = Array.from(container.querySelectorAll('[data-testid="activity-tool-name"]'));
    expect(names.map((el) => el.textContent)).toEqual([
      'Searched the web',
      'Read a page',
      'Image Generator · Generated an image',
      'E2Esaas · Create ticket',
      'Jira · Get issue by ID',
      'Ran code',
    ]);
    expect(container.textContent ?? '').not.toMatch(RAW);
    expect(names.map((el) => el.getAttribute('data-tool-id'))).toEqual(CALLS.map((call) => call.toolName));
  });
});
