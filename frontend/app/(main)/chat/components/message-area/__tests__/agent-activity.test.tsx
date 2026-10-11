import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
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

  it('labels a non-search multi-tool burst as "Ran N tools"', () => {
    renderTimeline([
      { type: 'tool_call', toolCallId: 'call-1', toolName: 'run_code', displayName: 'Ran code', status: 'completed' },
      { type: 'tool_call', toolCallId: 'call-2', toolName: 'install_packages', displayName: 'Installed packages', status: 'completed' },
    ]);

    expect(screen.getByText('Ran 2 tools')).toBeTruthy();
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

  it('uses the skill label map when displayName is absent — chats persisted before display_name existed', () => {
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'load_skill' })).toBe('Loaded skill');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'skill_search' })).toBe('Searched skills');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'skills_list' })).toBe('Listed skills');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'load_skill_resource' })).toBe('Loaded skill file');
    expect(toolActivityLabel({ type: 'tool_call', toolName: 'skill_manage' })).toBe('Managed skill');
  });

  it('still prefers a backend-provided displayName over the skill label map', () => {
    expect(
      toolActivityLabel({ type: 'tool_call', toolName: 'load_skill', displayName: 'Loaded skill docx' }),
    ).toBe('Loaded skill docx');
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

  it('shows the raw arguments when there is no summary', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'x', status: 'completed', args: '{"q":"hi"}' });

    expect(screen.getByText(/"q": "hi"/)).toBeTruthy();
  });

  it.each(['{}', 'null', '   '])('hides the Arguments section for empty args %j', (args) => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'x', status: 'completed', args });

    expect(screen.queryByText('Arguments')).toBeNull();
  });

  it('labels the arguments section', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'x', status: 'completed', args: '{"q":"hi"}' });

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
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'x', status: 'completed', resultPreview: preview });

    expect(screen.getByText(preview)).toBeTruthy();
  });

  it('shows the reason for a blocked call', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'x', status: 'blocked', resultPreview: 'denied by policy' });

    expect(screen.getByText('Blocked')).toBeTruthy();
    expect(screen.getByText('denied by policy')).toBeTruthy();
  });

  it('says a call is waiting for approval', () => {
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'x', status: 'awaiting_approval', resultPreview: 'Waiting for your approval' });

    expect(screen.getByText('Waiting for approval')).toBeTruthy();
  });

  it('copies the formatted payload', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    expand({ type: 'tool_call', toolCallId: 'c', toolName: 'x', status: 'completed', args: '{"q":"hi"}' });

    fireEvent.click(screen.getByRole('button', { name: /copy/i }));

    expect(writeText).toHaveBeenCalledWith('{\n  "q": "hi"\n}');
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

  it('does not auto-collapse when isStreaming transitions from true to false', () => {
    const parts: MessagePart[] = [{ type: 'tool_call', toolCallId: 'c1', toolName: 't' }];
    const { rerender } = renderCollapsible(parts, { isStreaming: true });
    expect(isContentHidden()).toBe(false);

    rerender(
      h(Theme, null, h(CollapsibleActivitySection, { parts, isStreaming: false }, h('div', { 'data-testid': 'child' }, 'child content'))),
    );
    // Stays expanded — collapse is user-driven only.
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
