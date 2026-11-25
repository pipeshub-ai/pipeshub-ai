import React from 'react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { getI18n } from 'react-i18next';
import '@/lib/__tests__/test-i18n';
import de from '@/lib/i18n/locales/de-DE.json';
import {
  AgentActivityTimeline,
  toolActivityLabel,
  hasMultiStepActivity,
  getVisibleRootParts,
  buildActivitySummary,
  CollapsibleActivitySection,
  ToolCallCard,
  formatToolPayload,
} from '../agent-activity';
import { TOOL_PROGRESS_STATUS, type MessagePart, type SSEPlanEvent, type StatusMessage } from '../../../types';
import testI18n from '@/lib/__tests__/test-i18n';

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

function renderCard(part: MessagePart) {
  return render(h(Theme, null, h(ToolCallCard, { part })));
}

const STATUS: StatusMessage = { id: 's1', status: 'executing', message: 'Using Jira Search...', timestamp: '' };

describe('getVisibleRootParts — the sign-in part', () => {
  it("isn't activity, so a reply with only an answer and the card has no timeline", () => {
    const parts: MessagePart[] = [
      { type: 'text', content: 'Drive needs more permission.', isFinal: true },
      { type: 'mcp_sign_in', servers: [{ instanceId: 'inst-drive', serverName: 'Drive', scopes: ['files.write'] }] },
    ];
    expect(getVisibleRootParts(parts, false)).toEqual([]);
  });
});

describe('AgentActivityTimeline — narration text', () => {
  it('renders a root-level narration text part', () => {
    renderTimeline([{ type: 'text', content: 'Let me check the test file first.' }]);

    expect(screen.getByText('Let me check the test file first.')).toBeTruthy();
  });

  it('renders two narration parts of the same run without a duplicate React key', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderTimeline([
      { type: 'text', content: 'I will find the CSV first.', runId: 'run-1' },
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'search', status: 'done' },
      { type: 'text', content: 'Found it, reading the first rows.', runId: 'run-1' },
    ]);

    expect(screen.getByText('I will find the CSV first.')).toBeTruthy();
    expect(screen.getByText('Found it, reading the first rows.')).toBeTruthy();
    const duplicateKeyWarnings = errorSpy.mock.calls.filter((args) =>
      String(args[0]).includes('Encountered two children with the same key'),
    );
    errorSpy.mockRestore();
    expect(duplicateKeyWarnings).toEqual([]);
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

  it('hides a not-yet-settled trailing text part even once a reasoning part follows it — it streams into AnswerContent, not the timeline, until settled', () => {
    // The trailing text hasn't been proven to be narration yet (that only
    // happens once `settleLastRootText()` runs, marking it `settled`) — it
    // stays live in `AnswerContent` (see `ChatResponse`) instead of
    // duplicating here, regardless of other activity already in the
    // transcript.
    const { container } = renderTimeline(
      [
        { type: 'text', content: 'Still working on this.' },
        { type: 'reasoning', content: 'thinking...' },
      ],
      { isStreaming: true },
    );

    expect(container.textContent).not.toContain('Still working on this.');
    expect(container.querySelector('.live-narration-text')).toBeNull();
  });

  it('hides a not-yet-settled trailing text part after a tool_call too — it streams into AnswerContent, not the timeline, until settled', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: 'Found something, let me dig deeper.' },
      ],
      { isStreaming: true },
    );

    expect(container.textContent).not.toContain('Found something, let me dig deeper.');
    expect(container.querySelector('.live-narration-text')).toBeNull();
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
  });

  it('renders a settled narration part as plain (non-live) text even in a multi-step stream, while the trailing unsettled one stays hidden', () => {
    const { container } = renderTimeline(
      [
        { type: 'text', content: 'Already settled narration.', settled: true },
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: 'Currently being written.' },
      ],
      { isStreaming: true },
    );

    expect(screen.getByText('Already settled narration.')).toBeTruthy();
    // The trailing, unsettled part streams into AnswerContent instead.
    expect(container.textContent).not.toContain('Currently being written.');
    expect(container.querySelectorAll('.live-narration-text').length).toBe(0);
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

  it('labels a non-search multi-tool burst as "Used N tools", the count the steps header uses (QA-FP4)', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'run_code', displayName: 'Ran code', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'install_packages', displayName: 'Installed packages', status: 'completed' },
    ]);

    expect(screen.getByText('Used 2 tools')).toBeTruthy();
  });

  it('labels a burst of skill tool calls as "Used N skills", not "Explored N searches" — skill_search matches /search/i too', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'skill_search', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'load_skill', status: 'completed' },
    ]);

    expect(screen.getByText('Used 2 skills')).toBeTruthy();
    expect(screen.queryByText(/Explored \d+ searches/)).toBeNull();
  });

  it('does not group tool calls separated by a text/reasoning part', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', displayName: 'Searched the web', status: 'completed' },
      { type: 'text', content: 'Found something interesting.' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'web_scrape', displayName: 'Read a web page', status: 'completed' },
    ]);

    expect(screen.queryByText(/Explored \d+ searches/)).toBeNull();
    expect(screen.getByText('Searched the web')).toBeTruthy();
    // TOOLNAMES: a built-in reads its own (translated) action, whatever English the server sent.
    expect(screen.getByText('Read a page')).toBeTruthy();
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

  it('names a built-in by its action when displayName is absent (TOOLNAMES)', () => {
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'web_search' })).toBe('Searched the web');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'retrieval__search_internal_knowledge' })).toBe(
      'Search internal knowledge',
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

  it('uses the skill label map when displayName is absent — chats persisted before display_name existed', () => {
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'load_skill' })).toBe('Loaded skill');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'skill_search' })).toBe('Searched skills');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'skills_list' })).toBe('Listed skills');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'load_skill_resource' })).toBe('Loaded skill file');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'skill_manage' })).toBe('Managed skill');
  });

  it('a built-in skill reads its translated action, not the English the server sent (TOOLNAMES)', () => {
    expect(
      toolActivityLabel({ type: 'tool_call', toolName: 'load_skill', displayName: 'Loaded skill docx' }),
    ).toBe('Loaded skill');
  });
});

describe('ToolCallCard — skill tool result preview gating', () => {
  it('hides the raw resultPreview for a skill tool call with no resultSummary (pre-summary chat)', () => {
    renderCard({
      type: 'tool_call',
      toolCallId: 'call-1',
      toolName: 'load_skill',
      status: 'completed',
      resultPreview: '# docx\n\nfull SKILL.md instructions body...',
    });

    fireEvent.click(screen.getByRole('button'));

    expect(screen.queryByText(/full SKILL\.md instructions body/)).toBeNull();
  });

  it('shows resultSummary (not the raw preview) for a skill tool call when both are present', () => {
    renderCard({
      type: 'tool_call',
      toolCallId: 'call-1',
      toolName: 'load_skill',
      status: 'completed',
      resultSummary: 'Loaded skill docx',
      resultPreview: '# docx\n\nfull SKILL.md instructions body...',
    });

    fireEvent.click(screen.getByRole('button'));

    expect(screen.getByText('Loaded skill docx')).toBeTruthy();
    expect(screen.queryByText(/full SKILL\.md instructions body/)).toBeNull();
  });

  it('keeps showing the raw resultPreview for a non-skill tool call with no resultSummary (regression guard)', () => {
    renderCard({
      type: 'tool_call',
      toolCallId: 'call-1',
      toolName: 'run_code',
      status: 'completed',
      resultPreview: '{"stdout": "hello"}',
    });

    fireEvent.click(screen.getByRole('button'));

    expect(screen.getByText(/"stdout": "hello"/)).toBeTruthy();
  });
});

describe('ToolCallCard — a call that went through an approval card', () => {
  it.each([
    [{ status: 'awaiting_approval' as const }, 'Needs approval'],
    [{ status: 'completed' as const, approved: true, approvalAnswer: 'allowed' as const }, 'Approved'],
    [{ status: 'blocked' as const, approvalAnswer: 'declined' as const }, 'Declined'],
    [{ status: 'blocked' as const, approvalAnswer: 'expired' as const }, 'Expired'],
  ])('says how it was answered on its row', (state, chip) => {
    renderCard({ type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', displayName: 'Create issue', ...state });
    expect(screen.getByText(chip)).toBeTruthy();
  });

  it('has no mark when no card was involved', () => {
    renderCard({ type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_search', status: 'completed' });
    for (const chip of ['Needs approval', 'Approved', 'Declined', 'Expired']) expect(screen.queryByText(chip)).toBeNull();
  });
});

describe('ToolCallCard — raw arguments and result', () => {
  function expand(part: MessagePart) {
    renderCard(part);
    fireEvent.click(screen.getAllByRole('button')[0]);
  }

  it('shows the args summary AND the raw arguments, pretty-printed', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'call-1',
      toolName: 'mcp_pangea_get_audit_logs',
      status: 'completed',
      argsSummary: 'start: "2026-09-01"',
      args: '{"start":"2026-09-01","limit":50}',
    });

    expect(screen.getByText('start: "2026-09-01"')).toBeTruthy();
    expect(screen.getByText(/"limit": 50/).textContent).toBe('{\n  "start": "2026-09-01",\n  "limit": 50\n}');
  });

  it('a code step shows the code it ran and what it printed, under its summaries (QAFIX2)', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'code-1',
      toolName: 'run_code',
      status: 'completed',
      argsSummary: 'python · print(sum(range(1, 11)))',
      args: JSON.stringify({ code: '# sum\nprint(sum(range(1, 11)))', language: 'python' }),
      resultSummary: 'exit 0 · 55',
      resultPreview: JSON.stringify({ stdout: '55\n', stderr: 'warn: slow\n', exit_code: 0, artifacts: [] }),
    });

    expect(screen.getByText('python · print(sum(range(1, 11)))')).toBeTruthy();
    expect(screen.getByText(/^# sum/).textContent).toBe('# sum\nprint(sum(range(1, 11)))');
    expect(screen.getByText('exit 0 · 55')).toBeTruthy();
    expect(screen.getByText(/^55/).textContent).toBe('55\nwarn: slow');
  });

  it('shows the raw arguments when there is no summary', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'mcp_demo_lookup', status: 'completed', args: '{"q":"hi"}' });

    expect(screen.getByText(/"q": "hi"/)).toBeTruthy();
  });

  it.each(['{}', 'null', '   '])('hides the Arguments section for empty args %j', (args) => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'mcp_demo_lookup', status: 'completed', args });

    expect(screen.queryByText('Arguments')).toBeNull();
  });

  it('labels the arguments section', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'mcp_demo_lookup', status: 'completed', args: '{"q":"hi"}' });

    expect(screen.getByText('Arguments')).toBeTruthy();
  });

  it('marks a failed call as an error, in red', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'c',
      toolName: 'mcp_github_create_issue',
      status: 'failed',
      resultSummary: 'The GitHub MCP server rejected the stored credentials (HTTP 401).',
    });

    const heading = screen.getByText('Error');
    expect(heading.style.color).toBe('var(--red-11)');
    expect(screen.getByText(/rejected the stored credentials/)).toBeTruthy();
  });

  it('shows the result summary AND the raw preview', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'c',
      toolName: 'mcp_pangea_get_audit_logs',
      status: 'completed',
      resultSummary: 'Returned 2 items in events',
      resultPreview: '{"events":[{"id":1},{"id":2}]}',
    });

    expect(screen.getByText('Returned 2 items in events')).toBeTruthy();
    expect(screen.getByText(/"events": \[/)).toBeTruthy();
  });

  it('shows a truncated (non-JSON) preview verbatim', () => {
    const preview = '{"events":[{"id":1},… [truncated: 9000 chars total]';
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'mcp_demo_lookup', status: 'completed', resultPreview: preview });

    expect(screen.getByText(preview)).toBeTruthy();
  });

  it('shows the reason for a blocked call', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'mcp_demo_lookup', status: 'blocked', resultPreview: 'denied by policy' });

    expect(screen.getByText('Blocked')).toBeTruthy();
    expect(screen.getByText('denied by policy')).toBeTruthy();
  });

  it('says a call is waiting for approval', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'mcp_demo_lookup', status: 'awaiting_approval', resultPreview: 'Waiting for your approval' });

    expect(screen.getByText('Waiting for approval')).toBeTruthy();
  });

  it('copies the formatted payload', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'mcp_demo_lookup', status: 'completed', args: '{"q":"hi"}' });

    fireEvent.click(screen.getByRole('button', { name: /copy/i }));

    expect(writeText).toHaveBeenCalledWith('{\n  "q": "hi"\n}');
  });
});

describe('ToolCallCard — a tool that is not from an MCP server', () => {
  function expand(part: MessagePart) {
    renderCard(part);
    fireEvent.click(screen.getAllByRole('button')[0]);
  }

  it('shows only the summaries, as before, without the raw payloads', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'c',
      toolName: 'load_skill',
      status: 'completed',
      argsSummary: 'Loading skill pptx',
      args: '{"name":"pptx"}',
      resultSummary: 'Loaded skill pptx',
      resultPreview: '# pptx',
    });

    expect(screen.getByText('Loading skill pptx')).toBeTruthy();
    expect(screen.getByText('Loaded skill pptx')).toBeTruthy();
    expect(screen.queryByText(/"name"/)).toBeNull();
    expect(screen.queryByRole('button', { name: /copy/i })).toBeNull();
  });

  it('shows the raw result, unformatted, only when there is no summary', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'list_toolsets', status: 'completed', resultPreview: '{"toolsets":[]}' });

    expect(screen.getByText('{"toolsets":[]}')).toBeTruthy();
  });

  it('ignores a result view', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'c',
      toolName: 'knowledgegraph__search',
      status: 'completed',
      resultSummary: 'Found 2 records',
      resultView: { kind: 'records', columns: ['Name'], rows: [{ cells: ['A'] }], total: 1 },
    });

    expect(screen.getByText('Found 2 records')).toBeTruthy();
    expect(screen.queryByTestId('tool-result-table')).toBeNull();
  });
});

describe('ToolCallCard — SQL query section', () => {
  function expand(part: MessagePart) {
    const { container } = renderCard(part);
    fireEvent.click(screen.getAllByRole('button')[0]);
    return container;
  }

  it('shows the executed SQL in a Query section', () => {
    const container = expand({
      type: 'tool_call',
      toolCallId: 'c',
      toolName: 'sql__execute_sql_query',
      status: 'completed',
      argsSummary: 'Running SQL query on "PostgreSQL"',
      args: JSON.stringify({ query: 'SELECT id, name FROM users WHERE active = true', source_name: 'PostgreSQL' }),
    });

    expect(screen.getByText('Query')).toBeTruthy();
    expect(screen.getByText('Running SQL query on "PostgreSQL"')).toBeTruthy();
    // Syntax highlighting splits the SQL across token spans.
    expect(container.textContent).toContain('SELECT id, name FROM users WHERE active = true');
    expect(screen.getByText('Copy')).toBeTruthy();
  });

  it('has no Query section for another tool with a "query" argument', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'web_search', status: 'completed', args: '{"query":"weather"}' });

    expect(screen.queryByText('Query')).toBeNull();
  });

  describe('a cut-off query', () => {
    // Cut inside the `--` comment, the copied prefix would still run, without its WHERE clause.
    const args = JSON.stringify({ query: 'SELECT id FROM users -- active only\nWHERE active = true' });
    const cutOff: MessagePart = {
      type: 'tool_call',
      toolCallId: 'c',
      toolName: 'sql__execute_sql_query',
      status: 'completed',
      args: args.slice(0, args.indexOf('\\n')),
    };

    it('offers no copy or download, and says only its start was kept', () => {
      const container = expand(cutOff);

      expect(container.textContent).toContain('SELECT id FROM users -- active only');
      expect(screen.queryByText('Copy')).toBeNull();
      expect(screen.queryByText('Download')).toBeNull();
      expect(screen.getByText("Only the start of this query was kept, so it can't be copied.")).toBeTruthy();
    });

    it("says so in the reader's language", async () => {
      const i18n = getI18n();
      const previous = i18n.language;
      if (!i18n.hasResourceBundle('de-DE', 'translation')) i18n.addResourceBundle('de-DE', 'translation', de);
      await i18n.changeLanguage('de-DE');
      try {
        expand(cutOff);
        expect(screen.getByText(de.chat.toolCard.queryCutOff)).toBeTruthy();
      } finally {
        await i18n.changeLanguage(previous);
      }
    });
  });
});

describe('ToolCallCard — a readable result', () => {
  function expand(part: MessagePart) {
    renderCard(part);
    fireEvent.click(screen.getAllByRole('button')[0]);
  }

  const SEARCH: MessagePart = {
    type: 'tool_call',
    toolCallId: 'c',
    toolName: 'mcp_atlassian_searchJiraIssuesUsingJql',
    status: 'completed',
    resultSummary: 'Found 23 issues',
    resultPreview: '{"issues":[{"key":"PA-1"}]}',
    resultView: {
      kind: 'records',
      columns: ['Key', 'Summary', 'Status'],
      rows: [
        { cells: ['PA-1', 'Login fails', 'In Progress'], url: 'https://example.atlassian.net/browse/PA-1' },
        { cells: ['PA-2', 'Crash on save', 'Done'], url: 'javascript:alert(1)' },
      ],
      total: 23,
    },
  };

  it('shows a table of what was found, linked, with the raw output behind a toggle', () => {
    expand(SEARCH);

    expect(screen.getByText('Found 23 issues')).toBeTruthy();
    const table = within(screen.getByTestId('tool-result-table'));
    expect(table.getByText('Summary')).toBeTruthy();
    expect(table.getByRole('link', { name: 'PA-1' }).getAttribute('href')).toBe('https://example.atlassian.net/browse/PA-1');
    expect(table.getByRole('link', { name: 'PA-1' }).getAttribute('rel')).toBe('noopener noreferrer');
    // Not a web link: shown as text.
    expect(table.queryByRole('link', { name: 'PA-2' })).toBeNull();
    expect(table.getByText('PA-2')).toBeTruthy();
    expect(screen.getByText('Showing 2 of 23')).toBeTruthy();
    expect(screen.queryByText(/"issues": \[/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /View raw/ }));
    expect(screen.getByText(/"issues": \[/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Hide raw/ }));
    expect(screen.queryByText(/"issues": \[/)).toBeNull();
  });

  it("shows one record's fields", () => {
    expand({
      ...SEARCH,
      resultSummary: 'PA-7 · Login fails',
      resultView: { kind: 'fields', fields: [{ label: 'Status', value: 'Done' }], url: 'https://example.atlassian.net/browse/PA-7' },
    });

    const fields = within(screen.getByTestId('tool-result-fields'));
    expect(fields.getByText('Status')).toBeTruthy();
    expect(fields.getByText('Done')).toBeTruthy();
    expect(fields.getByRole('link', { name: 'Open' }).getAttribute('href')).toBe('https://example.atlassian.net/browse/PA-7');
  });

  it.each([
    ['not a view', 'a table'],
    ['an unknown kind', { kind: 'html', html: '<b>x</b>' }],
    ['no rows', { kind: 'records', columns: ['A'], rows: [] }],
  ])('ignores %s and shows the raw output as before', (_label, resultView) => {
    expand({ ...SEARCH, resultView });

    expect(screen.queryByTestId('tool-result-table')).toBeNull();
    expect(screen.getByText(/"issues": \[/)).toBeTruthy();
  });

  it('shows a prose result as text, without repeating its first line', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'c',
      toolName: 'mcp_docs_search',
      status: 'completed',
      resultSummary: '# Results',
      resultPreview: '# Results\nThe page explains the setup.',
    });

    expect(screen.getByTestId('tool-result-text').textContent).toBe('# Results\nThe page explains the setup.');
    expect(screen.getAllByText(/# Results/)).toHaveLength(1);
  });

  it('loads no image and links only to the web from a summary', () => {
    expand({
      ...SEARCH,
      resultView: undefined,
      resultSummary: 'PA-1 · ![x](https://tracker.example/pixel.png) [click](javascript:alert(1)) [docs](https://example.com/d)',
    });

    expect(document.querySelector('img')).toBeNull();
    expect(screen.queryByRole('link', { name: 'click' })).toBeNull();
    const docs = screen.getByRole('link', { name: 'docs' });
    expect(docs.getAttribute('href')).toBe('https://example.com/d');
    expect(docs.getAttribute('target')).toBe('_blank');
  });
});

describe('formatToolPayload', () => {
  it('pretty-prints JSON', () => {
    expect(formatToolPayload('{"a":[1]}')).toBe('{\n  "a": [\n    1\n  ]\n}');
  });

  it('returns non-JSON text unchanged', () => {
    expect(formatToolPayload('plain text')).toBe('plain text');
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

  it("lets a running tool's row stand in for its plain status", () => {
    const { container } = renderTimeline(
      [{ type: 'tool_call', toolCallId: 'call-1', toolName: 'run_code', status: 'running' }],
      { isStreaming: true, currentStatus: { ...STATUS, status: 'running_tool', message: 'Using Run Code...' } },
    );

    expect(container.textContent).not.toContain('Using Run Code...');
  });

  it("still shows a running tool's own progress note, such as waiting for a sandbox", () => {
    const { container } = renderTimeline(
      [{ type: 'tool_call', toolCallId: 'call-1', toolName: 'run_code', status: 'running' }],
      { isStreaming: true, currentStatus: { ...STATUS, status: TOOL_PROGRESS_STATUS, message: 'Using Run Code... waiting in queue' } },
    );

    expect(container.textContent).toContain('waiting in queue');
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

  it('does not suppress status for unsettled trailing text — that text streams in AnswerContent, not the timeline', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: 'Let me analyze this for you.' },
      ],
      { isStreaming: true, currentStatus: STATUS },
    );

    // Unsettled text is filtered out of the root timeline, so it cannot hide status.
    expect(container.querySelector('.live-narration-text')).toBeNull();
    expect(container.textContent).toContain('Using Jira Search...');
  });

  it('shows the status entry when the trailing text part has empty content', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'text', content: '' },
      ],
      { isStreaming: true, currentStatus: STATUS },
    );

    // Empty unsettled text is filtered out — status remains the progress signal.
    expect(container.querySelector('.live-narration-text')).toBeNull();
    expect(container.textContent).toContain('Using Jira Search...');
  });

  it('suppresses the status entry when the last visible item is a reasoning block', () => {
    const { container } = renderTimeline(
      [{ type: 'reasoning', content: 'thinking about this...' }],
      { isStreaming: true, currentStatus: STATUS },
    );

    expect(screen.getByText('Thinking')).toBeTruthy();
    expect(container.textContent).not.toContain('Using Jira Search...');
  });

  it('suppresses the status entry when the last visible item is a running tool call', () => {
    const { container } = renderTimeline(
      [{ type: 'tool_call', toolCallId: 'call-1', toolName: 'jira.search_issues', status: 'running' }],
      { isStreaming: true, currentStatus: STATUS },
    );

    expect(container.textContent).not.toContain('Using Jira Search...');
  });

  it('suppresses the status entry when the last visible item is a tool group with a running member', () => {
    const { container } = renderTimeline(
      [
        { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
        { type: 'tool_call', toolCallId: 'call-2', toolName: 'jira.search_issues', status: 'running' },
      ],
      { isStreaming: true, currentStatus: STATUS },
    );

    expect(container.textContent).not.toContain('Using Jira Search...');
  });

  it('still shows the status entry when the last visible item is a completed tool call', () => {
    const { container } = renderTimeline(
      [{ type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' }],
      { isStreaming: true, currentStatus: STATUS },
    );

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

  it('hides unsettled trailing text while streaming a multi-step response too — it streams into AnswerContent instead', () => {
    const parts: MessagePart[] = [
      { type: 'tool_call', toolCallId: 'c1', toolName: 'search' },
      { type: 'text', content: 'candidate text' },
    ];
    expect(getVisibleRootParts(parts, true)).toEqual([parts[0]]);
  });

  it('shows settled narration regardless of streaming state', () => {
    const parts: MessagePart[] = [{ type: 'text', content: 'narration', settled: true }];
    expect(getVisibleRootParts(parts, true)).toEqual(parts);
    expect(getVisibleRootParts(parts, false)).toEqual(parts);
  });

  // CHAT-TL R3: a saved engine answer (run 9d1315d0) put an empty "Worked on this" header over the answer.
  it('shows nothing for a saved engine answer, whose steps the timeline does not draw', () => {
    const parts: MessagePart[] = [
      {
        type: 'engine_task',
        runId: 'run-1',
        taskId: 'run-1',
        frames: [{ tool: 'code_task', taskId: 'run-1', kind: 'started', text: 'what is our travel policy?' }],
      },
      { type: 'text', content: 'Our travel policy is…', runId: 'run-1', isFinal: true },
    ];
    expect(getVisibleRootParts(parts, false)).toEqual([]);
    expect(getVisibleRootParts(parts, true)).toEqual([]);
  });

  it('hides blank narration between tool calls', () => {
    const parts: MessagePart[] = [
      { type: 'tool_call', toolCallId: 'c1', toolName: 'search' },
      { type: 'text', content: '', settled: true },
      { type: 'reasoning', content: '  ' },
    ];
    expect(getVisibleRootParts(parts, false)).toEqual([parts[0]]);
  });
});

const summary = (parts: MessagePart[], plan?: SSEPlanEvent | null) => buildActivitySummary(parts, testI18n.t, plan);

const PLAN: SSEPlanEvent = {
  runId: 'run-1',
  currentTaskId: 't2',
  steps: [
    { taskId: 't1', title: 'Find the travel policy', status: 'completed', deps: [] },
    { taskId: 't2', title: 'Read the hotel limits', status: 'in_progress', deps: ['t1'] },
    { taskId: 't3', title: 'Write the summary', status: 'pending', deps: ['t2'] },
    { taskId: 't4', title: 'Check receipts rules', status: 'cancelled', deps: [] },
  ],
};

describe('buildActivitySummary', () => {
  it('describes a single tool call', () => {
    expect(summary([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }])).toBe('Used 1 tool');
  });

  it('describes multiple tool calls', () => {
    expect(
      summary([
        { type: 'tool_call', toolCallId: 'c1', toolName: 't' },
        { type: 'tool_call', toolCallId: 'c2', toolName: 't' },
      ]),
    ).toBe('Used 2 tools');
  });

  it('combines reasoning and tool segments', () => {
    expect(
      summary([
        { type: 'reasoning', content: 'thinking' },
        { type: 'tool_call', toolCallId: 'c1', toolName: 't' },
      ]),
    ).toBe('Thought about this · Used 1 tool');
  });

  it('describes sub-agent delegation', () => {
    expect(summary([{ type: 'sub_agent', runId: 'r1', parts: [] }])).toBe('Delegated to 1 sub-agent');
  });

  it('CTL-3: never says "Worked on this"; steps it cannot count are offered as steps', () => {
    expect(summary([{ type: 'text', content: 'narration', settled: true }])).toBe('Show steps');
  });

  it('CTL-3b: names each step by what the tool says it did, never by guessing from its name', () => {
    expect(
      summary([
        { type: 'tool_call', toolCallId: 'c1', toolName: 'knowledgegraph__search_entities', displayName: 'Searched the knowledge base' },
        { type: 'tool_call', toolCallId: 'c2', toolName: 'knowledgegraph__search_entities', displayName: 'Searched the knowledge base' },
        { type: 'tool_call', toolCallId: 'c3', toolName: 'knowledgegraph__fetch_record', displayName: 'Read the full record' },
      ]),
    ).toBe('Searched the knowledge base (2) · Read the full record');
  });

  it('CTL-3b: a connector tool with no display name says which app it used', () => {
    expect(summary([{ type: 'tool_call', toolCallId: 'c1', toolName: 'jira__create_issue' }])).toBe(
      'Used Jira: Create issue',
    );
    // A name with no app and no display name is still only counted.
    expect(summary([{ type: 'tool_call', toolCallId: 'c1', toolName: 'retrieval_search' }])).toBe('Used 1 tool');
  });

  it('CTL-3b: lists three kinds of step, then says how many more', () => {
    const tool = (id: string, displayName: string): MessagePart => ({ type: 'tool_call', toolCallId: id, toolName: `x__${id}`, displayName });
    expect(summary([tool('a', 'A'), tool('b', 'B'), tool('c', 'C'), tool('d', 'D'), tool('e', 'E')])).toBe('A · B · C · 2 more · Used 5 tools');
  });

  it('CTL-3b: ends with how long the run took, from the saved answer', () => {
    expect(
      summary([
        { type: 'tool_call', toolCallId: 'c1', toolName: 't', durationMs: 3_500 },
        { type: 'text', content: 'Done.', isFinal: true, elapsedMs: 42_400 },
      ]),
    ).toBe('Used 1 tool · 42s');
    expect(summary([{ type: 'text', content: 'Done.', isFinal: true, elapsedMs: 125_000 }])).toBe('Show steps · 2m 5s');
  });

  it('CTL-3: leads with the plan when the run planned two or more steps', () => {
    expect(summary([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }], PLAN)).toBe(
      'Completed 1 of 3 steps · Used 1 tool',
    );
    const one: SSEPlanEvent = { ...PLAN, steps: PLAN.steps.slice(0, 1) };
    expect(summary([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }], one)).toBe('Used 1 tool');
  });
});

describe('CollapsibleActivitySection', () => {
  function renderCollapsible(parts: MessagePart[], props: { isStreaming?: boolean } = {}) {
    return render(
      h(Theme, null, h(CollapsibleActivitySection, { parts, ...props }, h('div', { 'data-testid': 'child' }, 'child content'))),
    );
  }

  /** Children stay mounted for the height animation. "Hidden" means the
   * content panel is aria-hidden (and inert) so it is not reachable. */
  function isContentHidden() {
    const panel = screen.queryByTestId('child')?.parentElement;
    if (!panel) return true;
    return panel.getAttribute('aria-hidden') === 'true';
  }

  it('starts collapsed for historical (non-streaming) messages', () => {
    renderCollapsible([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }]);
    expect(isContentHidden()).toBe(true);
    expect(screen.getByText('Used 1 tool')).toBeTruthy();
  });

  it('starts expanded during streaming', () => {
    renderCollapsible(
      [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }],
      { isStreaming: true },
    );
    expect(isContentHidden()).toBe(false);
    expect(screen.getByText('Used 1 tool')).toBeTruthy();
  });

  it('toggles collapse on header click during streaming', () => {
    renderCollapsible(
      [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }],
      { isStreaming: true },
    );

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(isContentHidden()).toBe(true);

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(isContentHidden()).toBe(false);
  });

  it('toggles collapse on header click for historical messages', () => {
    renderCollapsible([{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }]);

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(isContentHidden()).toBe(false);

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(isContentHidden()).toBe(true);
  });

  it('CTL-3: collapses to its summary when the run finishes, as Claude does', () => {
    const parts: MessagePart[] = [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }];
    const { rerender } = renderCollapsible(parts, { isStreaming: true });
    expect(isContentHidden()).toBe(false);

    rerender(
      h(Theme, null, h(CollapsibleActivitySection, { parts, isStreaming: false }, h('div', { 'data-testid': 'child' }, 'child content'))),
    );
    expect(isContentHidden()).toBe(true);
    expect(screen.getByText('Used 1 tool')).toBeTruthy();
  });

  it('CTL-3: a section the user opened again while it ran stays open when it finishes', () => {
    const parts: MessagePart[] = [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }];
    const { rerender } = renderCollapsible(parts, { isStreaming: true });
    fireEvent.click(screen.getByText('Used 1 tool'));
    fireEvent.click(screen.getByText('Used 1 tool'));
    rerender(
      h(Theme, null, h(CollapsibleActivitySection, { parts, isStreaming: false }, h('div', { 'data-testid': 'child' }, 'child content'))),
    );
    expect(isContentHidden()).toBe(false);
  });

  it('keeps a user-collapsed section collapsed when streaming ends', () => {
    const parts: MessagePart[] = [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }];
    const { rerender } = renderCollapsible(parts, { isStreaming: true });

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(isContentHidden()).toBe(true);

    rerender(
      h(Theme, null, h(CollapsibleActivitySection, { parts, isStreaming: false }, h('div', { 'data-testid': 'child' }, 'child content'))),
    );
    expect(isContentHidden()).toBe(true);
  });

  it('marks collapsed content aria-hidden and inert so it is not focusable', () => {
    render(
      h(
        Theme,
        null,
        h(
          CollapsibleActivitySection,
          { parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }] },
          h('button', { 'data-testid': 'inner-control', type: 'button' }, 'inner'),
        ),
      ),
    );

    const panel = screen.getByTestId('inner-control').parentElement;
    expect(panel?.getAttribute('aria-hidden')).toBe('true');
    expect(panel?.hasAttribute('inert')).toBe(true);

    fireEvent.click(screen.getByText('Used 1 tool'));
    expect(panel?.hasAttribute('aria-hidden')).toBe(false);
    expect(panel?.hasAttribute('inert')).toBe(false);
  });
});

describe('AgentActivityTimeline — a message the user added while it worked (T2)', () => {
  it('shows it where the run took it, marked as the user\'s', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 't1', toolName: 'search', status: 'completed' },
      { type: 'user_input', content: 'Also check the 2025 policy', messageSeq: 1 },
      { type: 'text', content: 'Done.', isFinal: true },
    ]);
    const message = screen.getByTestId('steering-message');
    expect(message.textContent).toContain('You added');
    expect(message.textContent).toContain('Also check the 2025 policy');
    expect(message.textContent).not.toContain('The task ended');
  });

  it('says so when the task ended before it could use the message', () => {
    renderTimeline([{ type: 'user_input', content: 'Too late', messageSeq: 2, consumed: false }]);
    expect(screen.getByTestId('steering-message').textContent).toContain(
      'The task ended before it could use this message.',
    );
  });

  it('C-21b: names the files the message carried', () => {
    renderTimeline([
      {
        type: 'user_input',
        content: 'And this one',
        messageSeq: 1,
        attachments: [{ recordId: 'rec-1', virtualRecordId: 'v-1', recordName: 'q4.pdf', mimeType: 'application/pdf', extension: 'pdf' }],
      },
    ]);
    expect(screen.getByTestId('steering-attachments').textContent).toContain('q4.pdf');
  });

  it('makes a response multi-step, so its timeline is shown', () => {
    expect(hasMultiStepActivity([{ type: 'user_input', content: 'x' }, { type: 'text', content: 'y', isFinal: true }])).toBe(true);
    expect(getVisibleRootParts([{ type: 'user_input', content: 'x' }], true)).toHaveLength(1);
  });
});

describe('CTL-3: the plan checklist', () => {
  it('lists the plan above the steps with each step\'s state', () => {
    render(
      h(Theme, null, h(AgentActivityTimeline, {
        parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'search', status: 'completed' }],
        isStreaming: true,
        plan: PLAN,
      })),
    );
    const list = screen.getByRole('list', { name: 'Plan' });
    const items = within(list).getAllByRole('listitem');
    // What a reader hears: the icon's ligature is aria-hidden.
    const spoken = (item: HTMLElement) =>
      Array.from(item.children)
        .filter((child) => child.getAttribute('aria-hidden') !== 'true')
        .map((child) => child.textContent)
        .join('');
    expect(items.map(spoken)).toEqual([
      'Find the travel policyDone',
      'Read the hotel limitsIn progress',
      'Write the summaryTo do',
      'Check receipts rulesDropped',
    ]);
    expect(items[1].getAttribute('aria-current')).toBe('step');
  });

  it('shows no checklist for a single-step plan or none', () => {
    const parts: MessagePart[] = [{ type: 'tool_call', toolCallId: 'c1', toolName: 'search', status: 'completed' }];
    render(h(Theme, null, h(AgentActivityTimeline, { parts, plan: { ...PLAN, steps: PLAN.steps.slice(0, 1) } })));
    expect(screen.queryByRole('list', { name: 'Plan' })).toBeNull();
    cleanup();
    render(h(Theme, null, h(AgentActivityTimeline, { parts, plan: null })));
    expect(screen.queryByRole('list', { name: 'Plan' })).toBeNull();
  });

  it('shows the checklist even before the first step has a row', () => {
    render(h(Theme, null, h(AgentActivityTimeline, { parts: [], isStreaming: true, plan: PLAN })));
    expect(screen.getByRole('list', { name: 'Plan' })).toBeTruthy();
  });
});

describe('CTL-3b: each step shows how long it took', () => {
  it('a tool call over a second shows its duration; a quicker one shows none', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'c1', toolName: 'web_search', displayName: 'Searched the web', status: 'completed', durationMs: 3_400 },
    ]);
    expect(screen.getAllByTestId('step-duration').map((el) => el.textContent)).toEqual(['3s']);
    cleanup();
    renderTimeline([
      { type: 'tool_call', toolCallId: 'c2', toolName: 'web_fetch', displayName: 'Read a web page', status: 'completed', durationMs: 400 },
    ]);
    expect(screen.queryByTestId('step-duration')).toBeNull();
  });
});

describe('ToolCallCard — web tools list what they found', () => {
  function expand(part: MessagePart) {
    renderCard(part);
    fireEvent.click(screen.getAllByRole('button')[0]);
  }

  const WEB_SEARCH: MessagePart = {
    type: 'tool_call',
    toolCallId: 'w',
    toolName: 'dynamic__web_search',
    status: 'completed',
    argsSummary: 'Searched the web for "fed rates"',
    resultSummary: 'Found 12 results',
    resultView: {
      kind: 'records',
      columns: ['Title', 'Site', 'Date'],
      rows: [
        { cells: ['Fed holds rates', 'news.example.com', '2026-01-02'], url: 'https://news.example.com/fed' },
        { cells: ['Second result', 'other.org', ''], url: 'https://other.org/a' },
      ],
      total: 12,
    },
  };

  it('shows every search result with a link to it', () => {
    expand(WEB_SEARCH);

    const table = within(screen.getByTestId('tool-result-table'));
    expect(table.getByRole('link', { name: 'Fed holds rates' }).getAttribute('href')).toBe('https://news.example.com/fed');
    expect(table.getByText('other.org')).toBeTruthy();
    expect(screen.getByText('Showing 2 of 12')).toBeTruthy();
    // No raw payload toggle: the model-facing text isn't for people.
    expect(screen.queryByRole('button', { name: /View raw/ })).toBeNull();
  });

  it('shows nothing extra while the search is still running', () => {
    expand({ ...WEB_SEARCH, status: 'running' });

    expect(screen.queryByTestId('tool-result-table')).toBeNull();
  });

  it('names the page a fetch read', () => {
    expand({
      type: 'tool_call',
      toolCallId: 'f',
      toolName: 'dynamic__fetch_url',
      status: 'completed',
      resultSummary: 'Read “Fed holds rates” (news.example.com)',
      resultView: {
        kind: 'fields',
        fields: [{ label: 'Page', value: 'Fed holds rates' }, { label: 'Site', value: 'news.example.com' }],
        url: 'https://news.example.com/fed',
      },
    });

    expect(screen.getByText('news.example.com')).toBeTruthy();
  });
});

describe('getVisibleRootParts — an engine report saved as the answer (FIX-UI1)', () => {
  // A recorded IPL turn as Node saves it now: the report is the isFinal part, after the code_task row.
  const fixture = JSON.parse(
    readFileSync(path.join(__dirname, 'data/engine-report-turn.json'), 'utf8'),
  ) as { answer: string; parts: MessagePart[] };
  const [, call, child] = fixture.parts;
  const saved: MessagePart[] = [{ type: 'text', content: fixture.answer, isFinal: true }, call, child];

  it('the classic view shows the report once, as the answer, never as narration', () => {
    const visible = getVisibleRootParts(saved, false);
    expect(visible.some((part) => part.type === 'text')).toBe(false);
    expect(visible.map((part) => part.type)).toEqual(getVisibleRootParts(fixture.parts.slice(1), false).map((p) => p.type));
  });
});
