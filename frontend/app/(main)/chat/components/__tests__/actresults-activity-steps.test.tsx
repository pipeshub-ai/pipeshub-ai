/**
 * ACTRESULTS: the Activity panel's steps show what each tool call found, in the chat's own format: one line for
 * what was done and one for its outcome, the result one click away, its arguments behind "Details", repeated
 * calls grouped, lookups folded into one quiet line, a child run's steps nested, and only safe links clickable.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ActivityTool, DurableAgentRunHandle, MessagePart } from '@/chat/types';
import { toActivityView } from '@/chat/activity';
import { VECTOR_EXAMPLES, VECTOR_MASK } from '@/chat/__tests__/token-vector';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', () => ({
  apiClient: api,
  streamSSEGet: vi.fn(),
}));

import { ActivityPanelView } from '../activity-panel/activity-panel';
import { partOfTool, stepItems, stepSubject } from '../activity-panel/activity-steps';
import { forgetStepDetails } from '../activity-panel/use-step-detail';
import { ToolCallCard } from '../message-area/agent-activity';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };

beforeEach(() => {
  api.get.mockReset();
  forgetStepDetails();
});
afterEach(cleanup);

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

const tool = (over: Partial<ActivityTool> & { toolCallId: string; name: string }): ActivityTool => ({ status: 'completed', ...over });

/** Opens the one step (or the n-th) and returns its row. */
function open(n = 0): HTMLElement {
  const row = screen.getAllByTestId('activity-tool')[n]!;
  fireEvent.click(within(row).getByTestId('activity-tool-toggle'));
  return row;
}

const KNOWLEDGE = tool({
  toolCallId: 'k1',
  name: 'retrieval__search_internal_knowledge',
  displayName: 'Searched your Collections',
  argsSummary: 'Search: "Q3 release date"',
  args: '{"query":"Q3 release date"}',
  resultSummary: 'Retrieved 6 blocks from 3 documents\n- [Q3 Release Plan](https://drive.google.com/file/d/1AbC/view)\n- Eng weekly sync',
  resultPreview: '[{"recordName":"Q3 Release Plan"}]',
  durationMs: 1400,
});

describe('ACTRESULTS: a step reads in two lines, its result one click away', () => {
  it('shows what was done and the outcome, with no result, arguments or ids until opened', () => {
    panel([KNOWLEDGE]);
    const row = screen.getByTestId('activity-tool');
    expect(within(row).getByTestId('activity-tool-name').textContent).toBe('Searched your Collections');
    expect(within(row).getByTestId('activity-tool-subject').textContent).toBe(' · “Q3 release date”');
    expect(within(row).getByTestId('activity-tool-outcome').textContent).toContain('Retrieved 6 blocks from 3 documents');
    expect(within(row).getByTestId('activity-tool-duration').textContent).toBe('1s');
    expect(within(row).queryByTestId('tool-result')).toBeNull();
    expect(row.textContent).not.toContain('k1');
    expect(screen.getByText('Steps')).toBeTruthy();
  });

  it('opens to the chat\'s own result, links in a new tab; the arguments only behind Details', () => {
    panel([KNOWLEDGE]);
    const row = open();
    const link = within(row).getByRole('link', { name: 'Q3 Release Plan' });
    expect(link.getAttribute('href')).toBe('https://drive.google.com/file/d/1AbC/view');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(within(row).queryByTestId('tool-arguments')).toBeNull();
    const details = within(row).getByRole('button', { name: 'Details' });
    expect(details.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(details);
    expect(within(row).getByTestId('tool-arguments').textContent).toContain('Q3 release date');
    // The bounded preview the summary stands in for is one more click away.
    fireEvent.click(within(row).getByRole('button', { name: /View raw/ }));
    expect(row.textContent).toContain('"recordName": "Q3 Release Plan"');
  });

  it('a web search lists every result, each linked; a page read names the page', () => {
    panel([
      tool({
        toolCallId: 'w1',
        name: 'web_search',
        argsSummary: 'Searched the web for "SOC 2 onboarding"',
        resultSummary: 'Found 2 results',
        resultPreview: '',
        resultView: {
          kind: 'records',
          columns: ['Title', 'Site'],
          rows: [
            { cells: ['SOC 2 personnel controls', 'vanta.com'], url: 'https://www.vanta.com/soc-2' },
            { cells: ['Trust Services Criteria', 'aicpa.org'], url: 'https://www.aicpa.org/tsc' },
          ],
          total: 6,
        },
      }),
    ]);
    const row = open();
    expect(within(row).getByTestId('activity-tool-subject').textContent).toBe(' · “SOC 2 onboarding”');
    const table = within(row).getByTestId('tool-result-table');
    expect(within(table).getByRole('link', { name: 'SOC 2 personnel controls' }).getAttribute('href')).toBe('https://www.vanta.com/soc-2');
    expect(table.textContent).toContain('Showing 2 of 6');
  });

  it('a toolset call\'s JSON result has its links clickable, a javascript: URL left as text', () => {
    panel([
      tool({
        toolCallId: 's1',
        name: 'slack__fetch_slack_thread',
        displayName: 'Slack · Fetch slack thread',
        resultPreview:
          '{"messages":[{"text":"Orders go through https://it.acme.dev/order.","link":"https://acme.slack.com/archives/C05/p17"},{"text":"javascript:alert(1)"},{"text":"mailto:hr-help@acme.dev"}]}',
      }),
    ]);
    const row = open();
    expect(within(row).getByRole('link', { name: 'https://it.acme.dev/order' }).getAttribute('href')).toBe('https://it.acme.dev/order');
    expect(within(row).getByRole('link', { name: 'https://acme.slack.com/archives/C05/p17' })).toBeTruthy();
    expect(within(row).getByRole('link', { name: 'mailto:hr-help@acme.dev' }).getAttribute('href')).toBe('mailto:hr-help@acme.dev');
    expect(row.textContent).toContain('javascript:alert(1)');
    for (const link of within(row).getAllByRole('link')) {
      expect(link.getAttribute('href')).toMatch(/^(https?:|mailto:)/);
      expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    }
  });

  it('an MCP call shows its table and its raw result behind View raw', () => {
    panel([
      tool({
        toolCallId: 'm1',
        name: 'mcp_linear_list_issues',
        displayName: 'Linear · List issues',
        args: '{"team":"ONB"}',
        resultPreview: '{"issues":[{"id":"ONB-12"}]}',
        resultView: { kind: 'records', columns: ['Issue'], rows: [{ cells: ['ONB-12 Automate accounts'], url: 'https://linear.app/acme/issue/ONB-12' }], total: 1 },
      }),
    ]);
    const row = screen.getByTestId('activity-tool');
    expect(within(row).getByTestId('activity-tool-outcome').textContent).toContain('Found 1 result');
    open();
    expect(within(row).getByRole('link', { name: 'ONB-12 Automate accounts' })).toBeTruthy();
    expect(within(row).getByRole('button', { name: /View raw/ })).toBeTruthy();
  });

  it('run_code shows what it printed', () => {
    panel([
      tool({
        toolCallId: 'r1',
        name: 'run_code',
        args: '{"code":"print(48)","language":"python"}',
        argsSummary: 'Running python',
        resultSummary: 'Exit code 0',
        resultPreview: '{"stdout":"Rows read: 48\\n","stderr":"FutureWarning: deprecated\\n","exit_code":0}',
      }),
    ]);
    const row = open();
    expect(row.textContent).toContain('Rows read: 48');
    expect(row.textContent).toContain('FutureWarning: deprecated');
  });

  it('an empty result says No results; a failure says what went wrong in words, its detail one click away', () => {
    panel([
      tool({ toolCallId: 'e1', name: 'jira__search_issues', resultPreview: '[]' }),
      tool({ toolCallId: 'f1', name: 'jira__get_issue', status: 'failed', failure: { code: 'not_found', detail: 'Issue HR-88 does not exist' } }),
    ]);
    const [empty, failed] = screen.getAllByTestId('activity-tool');
    expect(within(empty!).getByTestId('activity-tool-outcome').textContent).toContain('No results');
    expect(within(failed!).getByTestId('activity-tool-failure').textContent).toMatch(/Jira/);
    expect(within(failed!).queryByTestId('activity-tool-detail')).toBeNull();
    fireEvent.click(within(failed!).getByTestId('activity-tool-toggle'));
    expect(within(failed!).getByTestId('activity-tool-detail').textContent).toContain('HR-88');
    expect(within(failed!).queryByTestId('tool-result')).toBeNull();
  });

  it('an approved call says so; a waiting one says where to decide; a declined one has no result', () => {
    panel([
      tool({ toolCallId: 'a1', name: 'slack__send_message', approvalAnswer: 'allowed', resultSummary: 'Message sent to #eng' }),
      tool({
        toolCallId: 'a2',
        name: 'gmail__send',
        status: 'awaiting_approval',
        reason: { rule: 'flow_external_write', sensitivity: 'private', sources: [], moreSources: 0, conversationGrant: false },
      }),
      tool({ toolCallId: 'a3', name: 'drive__share', status: 'denied', approvalAnswer: 'declined', refusal: { rule: 'interaction_denied' } }),
    ]);
    const [allowed, waiting, declined] = screen.getAllByTestId('activity-tool');
    expect(within(allowed!).getByText('Approved')).toBeTruthy();
    expect(within(waiting!).getByTestId('activity-tool-reason').textContent).toBe(
      'This call waits for your approval. Decide on the approval card in the chat.',
    );
    // The approval card's own explanation is one click away, not in the step's one line.
    fireEvent.click(within(waiting!).getByTestId('activity-tool-toggle'));
    expect(within(waiting!).getByTestId('activity-tool-why').textContent!.length).toBeGreaterThan(20);
    expect(within(declined!).getByText('Denied')).toBeTruthy();
    fireEvent.click(within(declined!).getByTestId('activity-tool-toggle'));
    expect(within(declined!).queryByTestId('tool-result')).toBeNull();
  });

  it('a long result keeps the chat\'s bound and scrolls inside its box', () => {
    const long = `${'Employee handbook. '.repeat(120)}`.slice(0, 2000);
    panel([tool({ toolCallId: 'l1', name: 'fetch_full_record', resultPreview: long })]);
    const row = open();
    const preview = within(row).getByTestId('tool-result-preview');
    expect(preview.textContent).toHaveLength(2000);
  });

  it('a secret in a result, a summary or the arguments is masked, as in the chat', () => {
    const secret = VECTOR_EXAMPLES[0]!.secret;
    panel([
      tool({
        toolCallId: 'x1',
        name: 'okta__get_token',
        argsSummary: `Get token: "${secret}"`,
        args: JSON.stringify({ token: secret }),
        resultSummary: `Token ${secret}`,
        resultPreview: JSON.stringify({ token: secret }),
      }),
    ]);
    const row = open();
    fireEvent.click(within(row).getByRole('button', { name: 'Details' }));
    fireEvent.click(within(row).getByRole('button', { name: /View raw/ }));
    expect(row.textContent).not.toContain(secret);
    expect(row.textContent).toContain(VECTOR_MASK);
  });
});

describe('ACTRESULTS: a long run stays readable', () => {
  const search = (id: string, q: string, preview = '[{"ts":"1"}]'): ActivityTool =>
    tool({ toolCallId: id, name: 'slack__search_messages', displayName: 'Slack · Search messages', argsSummary: `Search messages: "${q}"`, resultPreview: preview });

  it('folds tool lookups into one quiet line and groups the same tool called in a row, in order', () => {
    const items = stepItems([
      tool({ toolCallId: 'q1', name: 'search_tools' }),
      tool({ toolCallId: 'q2', name: 'fetch_tools' }),
      search('s1', 'rollback'),
      search('s2', 'canary'),
      search('s3', 'revert', '[]'),
      tool({ toolCallId: 'j1', name: 'jira__get_issue' }),
      search('s4', 'hotfix'),
      tool({ toolCallId: 'k1', name: 'load_skill' }),
    ]);
    expect(items.map((item) => item.kind)).toEqual(['quiet', 'group', 'step', 'step', 'quiet']);
    expect(items[0]).toMatchObject({ lookup: 'tools' });
    expect(items[4]).toMatchObject({ lookup: 'skills' });
  });

  it('never groups a call waiting for a person, or one that started a child run', () => {
    const items = stepItems([
      search('s1', 'a'),
      { ...search('s2', 'b'), status: 'awaiting_approval' },
      search('s3', 'c'),
    ]);
    expect(items.map((item) => item.kind)).toEqual(['step', 'step', 'step']);
  });

  it('a group row counts its calls and opens to each with its own outcome', () => {
    panel([search('s1', 'rollback'), search('s2', 'canary'), { ...search('s3', 'revert'), status: 'failed', failure: { code: 'timeout' } }]);
    const group = screen.getByTestId('activity-step-group');
    expect(within(group).getByTestId('activity-step-group-name').textContent).toBe('Slack · Search messages (3 times)');
    expect(group.textContent).toContain('2 finished · 1 failed');
    expect(within(group).queryAllByTestId('activity-tool')).toHaveLength(0);
    fireEvent.click(within(group).getAllByRole('button')[0]!);
    const rows = within(group).getAllByTestId('activity-tool');
    expect(rows.map((row) => within(row).getByTestId('activity-tool-subject').textContent)).toEqual([' · “rollback”', ' · “canary”', ' · “revert”']);
  });

  it('a quiet line names no tool id', () => {
    panel([tool({ toolCallId: 'q1', name: 'pipeshub@lazy/search_tools' }), KNOWLEDGE]);
    expect(screen.getByTestId('activity-step-quiet').textContent).toContain('Looked up available tools');
    expect(document.body.textContent).not.toContain('search_tools');
  });
});

describe('ACTRESULTS: a child run\'s steps nest under the call that started it', () => {
  it('reads the child\'s timeline when opened and draws its steps and commands', async () => {
    api.get.mockResolvedValue({
      data: {
        runId: 'child-1',
        parentRunId: RUN.runId,
        status: 'completed',
        parts: [
          { type: 'tool_call', toolCallId: 'ct1', toolName: 'read_sandbox_file', status: 'completed', resultSummary: 'Read src/export.ts' },
        ] as MessagePart[],
        engine: null,
        approvals: [],
        children: [],
        merge: null,
        budget: null,
      },
    });
    panel([
      tool({
        toolCallId: 'c1',
        name: 'code_task',
        child: { runId: 'child-1', role: 'coder', status: 'completed', codeTaskGoal: 'Add a checklist export' },
        resultSummary: 'Sub-agent completed',
      }),
    ]);
    expect(api.get).not.toHaveBeenCalled();
    const row = open();
    await waitFor(() => expect(within(row).getByTestId('activity-child-steps').textContent).toContain('Read src/export.ts'));
    expect(api.get.mock.calls[0]![0]).toContain('/runs/child-1/timeline');
    expect(within(row).getByTestId('activity-child-steps').textContent).toContain('Add a checklist export');
  });
});

describe('ACTRESULTS: the server\'s step fields are checked before they are shown', () => {
  it('keeps what the chat step carries and drops a malformed child or answer', () => {
    const view = toActivityView({
      runId: 'r',
      status: 'running',
      commands: [],
      omittedCommands: 0,
      tools: [
        {
          toolCallId: 't1',
          name: 'jira__get_issue',
          status: 'completed',
          args: '{"key":"PAY-1"}',
          argsSummary: 'Get issue: "PAY-1"',
          durationMs: 800,
          resultPreview: '{"key":"PAY-1"}',
          resultSummary: 'Issue fetched',
          resultView: { kind: 'fields', fields: [{ label: 'Status', value: 'Done' }] },
          approvalAnswer: 'maybe',
          child: { runId: 'c', status: 'exploded' },
        },
      ],
      tasks: [],
      files: [],
      followups: [],
    });
    const [step] = view!.tools;
    expect(step).toMatchObject({ args: '{"key":"PAY-1"}', argsSummary: 'Get issue: "PAY-1"', durationMs: 800, resultSummary: 'Issue fetched' });
    expect(step!.approvalAnswer).toBeUndefined();
    expect(step!.child).toBeUndefined();
    expect(partOfTool(step!)).toMatchObject({ type: 'tool_call', toolName: 'jira__get_issue', resultPreview: '{"key":"PAY-1"}' });
  });

  it('names a step\'s subject from its quoted query or the page it read', () => {
    expect(stepSubject('Search messages: "release"')).toBe('release');
    expect(stepSubject('Reading https://docs.acme.dev/release/')).toBe('docs.acme.dev/release');
    expect(stepSubject('Marking task complete')).toBeUndefined();
  });
});

describe('ACTRESULTS: the chat\'s own step makes links in a raw result clickable too', () => {
  it('links http(s) in a plain preview, never a javascript: URL', () => {
    render(
      <Theme>
        <ToolCallCard
          part={{
            type: 'tool_call',
            toolCallId: 'p1',
            toolName: 'fetch_full_record',
            status: 'completed',
            resultPreview: 'See https://docs.acme.dev/a and javascript:alert(1)',
          }}
        />
      </Theme>,
    );
    fireEvent.click(screen.getAllByRole('button')[0]!);
    expect(screen.getByRole('link', { name: 'https://docs.acme.dev/a' }).getAttribute('target')).toBe('_blank');
    expect(screen.queryByRole('link', { name: /javascript/ })).toBeNull();
    expect(document.body.textContent).toContain('javascript:alert(1)');
  });
});

describe('ACTRESULTS: a step\'s result is read when it is opened, once per fingerprint', () => {
  const line = (detail: string): unknown => ({
    toolCallId: 'k1',
    name: 'slack__search_messages',
    displayName: 'Slack · Search messages',
    status: 'completed',
    argsSummary: 'Search messages: "rollback"',
    outcome: { kind: 'summary', text: 'Found 14 messages' },
    detail,
  });
  const served = (fingerprint: string, preview: string) => ({
    data: {
      step: {
        toolCallId: 'k1',
        fingerprint,
        args: '{"query":"rollback"}',
        argsSummary: 'Search messages: "rollback"',
        resultSummary: 'Found 14 messages',
        resultPreview: preview,
      },
    },
  });

  it('shows the server\'s outcome without reading anything, then reads the result once when opened', async () => {
    api.get.mockResolvedValue(served('fp-1', '{"link":"https://acme.slack.com/archives/C01/p1"}'));
    panel([line('fp-1')]);
    const row = screen.getByTestId('activity-tool');
    expect(within(row).getByTestId('activity-tool-outcome').textContent).toContain('Found 14 messages');
    expect(api.get).not.toHaveBeenCalled();

    const toggle = within(row).getByTestId('activity-tool-toggle');
    fireEvent.click(toggle);
    expect(within(row).getByTestId('activity-tool-loading')).toBeTruthy();
    await waitFor(() => expect(within(row).getByTestId('tool-result')).toBeTruthy());
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(String(api.get.mock.calls[0]![0])).toMatch(/\/conversations\/conv-1\/runs\/run-1\/steps\/k1$/);
    fireEvent.click(within(row).getByRole('button', { name: /View raw/ }));
    expect(within(row).getByRole('link', { name: 'https://acme.slack.com/archives/C01/p1' })).toBeTruthy();

    // Closed and opened again: the result is kept, not read again.
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(within(row).getByTestId('tool-result')).toBeTruthy();
    expect(api.get).toHaveBeenCalledTimes(1);
  });

  it('a new fingerprint (the result arrived or changed) reads it again', async () => {
    api.get.mockResolvedValueOnce(served('fp-1', 'first')).mockResolvedValueOnce(served('fp-2', 'second result'));
    const first = panel([line('fp-1')]);
    fireEvent.click(within(screen.getByTestId('activity-tool')).getByTestId('activity-tool-toggle'));
    await waitFor(() => expect(screen.getByTestId('tool-result')).toBeTruthy());
    first.unmount();
    panel([line('fp-2')]);
    fireEvent.click(within(screen.getByTestId('activity-tool')).getByTestId('activity-tool-toggle'));
    await waitFor(() => expect(screen.getByTestId('tool-result')).toBeTruthy());
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('a read that fails says so, and opening the step again retries', async () => {
    api.get.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(served('fp-1', 'ok'));
    panel([line('fp-1')]);
    const row = screen.getByTestId('activity-tool');
    const toggle = within(row).getByTestId('activity-tool-toggle');
    fireEvent.click(toggle);
    await waitFor(() => expect(within(row).getByTestId('activity-tool-unavailable')).toBeTruthy());
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    await waitFor(() => expect(within(row).getByTestId('tool-result')).toBeTruthy());
  });

  it('a malformed step from the server is not shown', async () => {
    api.get.mockResolvedValue({ data: { step: { toolCallId: 'k1' } } });
    panel([line('fp-1')]);
    const row = screen.getByTestId('activity-tool');
    fireEvent.click(within(row).getByTestId('activity-tool-toggle'));
    await waitFor(() => expect(within(row).getByTestId('activity-tool-unavailable')).toBeTruthy());
  });
});

describe('WP115-FU-1: an engine run opened in the panel lists its own tool steps', () => {
  const line = (kind: string, text: string, data: Record<string, unknown>, seq: number) => ({
    tool: 'code_task', taskId: RUN.runId, kind, text, data, turnId: 'trn_1', seq,
  });
  const FRAMES = [
    line('started', 'Compare Onyx pricing', {}, 1),
    line('command', 'ls', { callId: 'c1' }, 2),
    line('command_done', '', { callId: 'c1', exitCode: 0, status: 'completed' }, 3),
    line('tool', 'mcp_e2esaas_lookup_note', { callId: 't1', displayName: 'E2Esaas · lookup_note' }, 4),
    line('tool_result', 'mcp_e2esaas_lookup_note', { callId: 't1', status: 'completed', result: 'The review is due on Friday.' }, 5),
    line('file', 'notes.md', { change: 'added' }, 6),
  ];
  const timeline = (status = 'completed') => ({
    data: {
      runId: RUN.runId,
      status,
      parts: [{ type: 'engine_task', runId: RUN.runId, taskId: RUN.runId, frames: FRAMES }] as unknown as MessagePart[],
      engine: { taskId: RUN.runId, frames: FRAMES, truncated: false },
      approvals: [],
      children: [],
      merge: null,
      budget: null,
    },
  });
  const HELD = tool({ toolCallId: 'a1', name: 'web@searxng/fetch_url', displayName: 'Read a page', status: 'awaiting_approval' });

  function enginePanel(tools: unknown[], engine = true) {
    const view = toActivityView({
      runId: RUN.runId, status: 'completed', engine, commands: [], omittedCommands: 0, tools, tasks: [], files: [], followups: [],
    });
    return render(
      <Theme>
        <ActivityPanelView run={RUN} state={{ view: view!, connection: 'live' }} variant="pane" onClose={vi.fn()} />
      </Theme>,
    );
  }

  it('reads its own timeline and draws its tool steps beside its held asks, not its commands or files', async () => {
    api.get.mockResolvedValue(timeline());
    enginePanel([HELD]);
    await waitFor(() => expect(screen.getAllByTestId('activity-tool')).toHaveLength(2));
    expect(api.get.mock.calls[0]![0]).toContain(`/runs/${RUN.runId}/timeline`);
    const names = screen.getAllByTestId('activity-tool-name').map((node) => node.textContent);
    expect(names[0]).toBe('E2Esaas · Lookup note');
    expect(names[1]).toContain('Read a page');
    expect(document.body.textContent).not.toContain('mcp_e2esaas');
  });

  it('an engine run with no held ask still lists its steps', async () => {
    api.get.mockResolvedValue(timeline());
    enginePanel([]);
    await waitFor(() => expect(screen.getAllByTestId('activity-tool')).toHaveLength(1));
  });

  it('a step its timeline already names as held is listed once', async () => {
    api.get.mockResolvedValue(timeline());
    enginePanel([tool({ toolCallId: 't1', name: 'mcp_e2esaas_lookup_note', status: 'completed' })]);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    await waitFor(() => expect(screen.getAllByTestId('activity-tool')).toHaveLength(1));
  });

  it('a loop run reads no timeline: its tools are its steps', () => {
    enginePanel([HELD], false);
    expect(screen.getAllByTestId('activity-tool')).toHaveLength(1);
    expect(api.get).not.toHaveBeenCalled();
  });
});
