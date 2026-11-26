/**
 * WP-18 (C10.02, UX-06): a typed error the reply shows offers its next step as one click when it is one (try again,
 * new chat, reload), and nothing when the step is the person's own (ask an admin); a tool the agent found but cannot
 * use here is a row with Connect when only the person's sign-in is missing.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';

const sent = vi.hoisted(() => ({ stream: vi.fn(), build: vi.fn(), dispatch: vi.fn() }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: sent.stream }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: sent.build }));
vi.mock('../../../store', () => ({ useChatStore: { getState: () => ({ activeSlotId: 'slot-1' }) } }));
vi.mock('@/lib/store/command-store', () => ({ useCommandStore: { getState: () => ({ dispatch: sent.dispatch }) } }));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => React.createElement('a', { href, ...rest }, children),
}));

import { ErrorNextStep } from '../error-next-step';
import { ToolUnavailableRows, connectHref, unavailableFromSearch, uniqueUnavailable } from '../tool-unavailable-rows';
import { ToolCallCard } from '../agent-activity';

const h = React.createElement;
beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  sent.stream.mockReset();
  sent.build.mockReset().mockReturnValue({ query: 'q' });
  sent.dispatch.mockReset();
});
afterEach(() => cleanup());

const show = (error: Parameters<typeof ErrorNextStep>[0]['error']) =>
  render(h(Theme, null, h(ErrorNextStep, { error, question: 'Build the Q3 chart' })));

describe('UX-06: the next step of a typed error is one click (C10.02)', () => {
  it('UX-06: ACCESS_POLICY_UNAVAILABLE (an outage) waits, then sends the question again', () => {
    show({ code: 'HTTP_AGENT_HARNESS_ACCESS_POLICY_UNAVAILABLE', source: 'admission' });
    const button = screen.getByRole('button', { name: /Try again/ });
    expect(screen.getByTestId('error-next-step').getAttribute('data-step')).toBe('wait_then_retry');
    fireEvent.click(button);
    expect(sent.build).toHaveBeenCalledWith('slot-1', 'Build the Q3 chart');
    expect(sent.stream).toHaveBeenCalledWith('slot-1', 'Build the Q3 chart', { query: 'q' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it('UX-06: SESSION_ERASED starts a new chat', () => {
    show({ code: 'HTTP_AGENT_HARNESS_SESSION_ERASED', source: 'admission' });
    fireEvent.click(screen.getByRole('button', { name: /New chat/ }));
    expect(sent.dispatch).toHaveBeenCalledWith('newChat');
    expect(sent.stream).not.toHaveBeenCalled();
  });

  it('UX-06: a run with no free sandbox offers to try again', () => {
    show({ code: 'sandbox_unavailable', source: 'run' });
    expect(screen.getByRole('button', { name: /Try again/ })).toBeTruthy();
  });

  it.each([
    ['run', 'gvisor_unavailable'],
    ['answer', 'budget_exhausted'],
    ['answer', 'budget_insufficient'],
    ['answer', 'content_filter'],
  ] as const)('UX-06: %s %s asks the person (admin, or a change) in words, with no button', (source, code) => {
    show({ code, source });
    expect(screen.queryByTestId('error-next-step')).toBeNull();
  });

  it('UX-06: an error of no harness source (a legacy failure) offers nothing', () => {
    show({ code: 'mcp_server_config_missing' });
    expect(screen.queryByTestId('error-next-step')).toBeNull();
    cleanup();
    show(undefined);
    expect(screen.queryByTestId('error-next-step')).toBeNull();
  });
});

describe('UX-06: a tool the agent found but cannot use here', () => {
  it('UX-06: one not connected for the person is a row with Connect to its sign-in page', () => {
    render(h(Theme, null, h(ToolUnavailableRows, {
      tools: [
        { tool: 'create_issue', toolset: 'jira', reason: 'not_authenticated' },
        { tool: 'send_message', toolset: 'slack', reason: 'not_attached' },
      ],
    })));
    const rows = screen.getAllByTestId('tool-unavailable-row');
    expect(rows[0].textContent).toContain("Jira · Create issue isn't connected");
    expect(screen.getByRole('link', { name: 'Connect Jira · Create issue' }).getAttribute('href')).toBe(
      '/workspace/actions/personal/?toolsetType=jira',
    );
    expect(rows[1].textContent).toBe("Slack · Send message isn't available to this agent");
    expect(rows[1].querySelector('a')).toBeNull();
    expect(connectHref('mcp_e2esaas')).toBe('/workspace/mcp-servers/personal/');
  });

  // The search step's own result, as the golden `agent_search_tools_unavailable` keeps it, live and saved alike.
  const SEARCH = {
    type: 'tool_call' as const,
    toolCallId: 'call-find',
    toolName: 'search_tools',
    status: 'completed' as const,
    resultPreview: JSON.stringify({
      matches: [],
      unavailable: [
        { name: 'jira__create_issue', toolset: 'jira', description: 'Create an issue.', reason: 'not_authenticated' },
        { name: 'goldencrm__close_deal', toolset: 'goldencrm', description: 'Close a deal.', reason: 'not_attached' },
      ],
      message: 'No attached tool matched your query, but a matching tool exists.',
    }),
  };

  it('UX-06: the search step names what it found but cannot use; nothing else, never its message', () => {
    expect(unavailableFromSearch(SEARCH)).toEqual([
      { tool: 'create_issue', toolset: 'jira', reason: 'not_authenticated' },
      { tool: 'close_deal', toolset: 'goldencrm', reason: 'not_attached' },
    ]);
    expect(unavailableFromSearch({ ...SEARCH, toolName: 'web_search' })).toEqual([]);
    expect(unavailableFromSearch({ ...SEARCH, resultPreview: '{"matches": [], "unav' })).toEqual([]);
    expect(uniqueUnavailable([...unavailableFromSearch(SEARCH), ...unavailableFromSearch(SEARCH)])).toHaveLength(2);
  });

  it('UX-06: the search step shows its Connect row without being opened', () => {
    render(h(Theme, null, h(ToolCallCard, { part: SEARCH })));
    const rows = screen.getAllByTestId('tool-unavailable-row');
    expect(rows.map((r) => r.textContent)).toEqual([
      "Jira · Create issue isn't connectedConnect",
      "Goldencrm · Close deal isn't available to this agent",
    ]);
    expect(screen.getByRole('link', { name: 'Connect Jira · Create issue' })).toBeTruthy();
    expect(document.body.textContent).not.toContain('No attached tool matched');
  });
});
