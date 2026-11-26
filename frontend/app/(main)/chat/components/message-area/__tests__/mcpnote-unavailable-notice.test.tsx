/**
 * MCPNOTE-1: a run that went without some of the person's MCP servers says which ones, why, and what fixes it:
 * signing in right from the answer (the MCP OAuth popup), or the page where they or an admin fix it.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart } from '../../../types';
import { createAGUIEventHandler } from '../../../agui-event-handler';
import { RunNotices } from '../agent-activity';

const popup = vi.hoisted(() => ({ start: vi.fn(), options: null as null | { onVerified?: () => void } }));
vi.mock('@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup', () => ({
  useMcpOAuthPopup: (options: { onVerified?: () => void }) => {
    popup.options = options;
    return { startOAuthPopup: popup.start };
  },
  mcpOAuthFailureMessage: () => 'failed',
}));

const admin = vi.hoisted(() => ({ isAdmin: false }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (select: (state: unknown) => unknown) => select(admin),
  selectIsAdmin: (state: { isAdmin: boolean }) => state.isAdmin,
}));

beforeEach(() => {
  popup.start.mockReset();
  admin.isAdmin = false;
});
afterEach(() => cleanup());

const unused = (item: string, reason?: string, instance?: string): MessagePart => ({
  type: 'notice',
  code: 'mcp_unavailable',
  item,
  ...(reason ? { reason } : {}),
  ...(instance ? { instance } : {}),
  runId: 'run-1',
});

const notes = () => screen.getAllByRole('note').map((note) => note.textContent);

describe('MCPNOTE-1: the answer names each MCP server it went without', () => {
  it('one server that wants a sign-in reads plainly, with a button that opens its sign-in', () => {
    render(<Theme><RunNotices parts={[unused('Atlassian Rovo', 'sign_in', 'mcp-rovo')]} /></Theme>);
    expect(notes()).toEqual(["Atlassian Rovo wasn't used for this answer: sign in to it first.Sign in to Atlassian Rovo"]);
    expect(screen.queryByRole('link')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to Atlassian Rovo' }));
    expect(popup.start).toHaveBeenCalledWith('mcp-rovo');
  });

  it('after the sign-in it says so and to ask again', async () => {
    render(<Theme><RunNotices parts={[unused('Atlassian Rovo', 'sign_in', 'mcp-rovo')]} /></Theme>);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to Atlassian Rovo' }));
    React.act(() => popup.options?.onVerified?.());
    expect(await screen.findByText('Signed in to Atlassian Rovo. Ask again to use it.')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('several servers with one reason read as one sentence, each once, each with its own sign-in', () => {
    render(
      <Theme>
        <RunNotices
          parts={[
            unused('Atlassian Rovo', 'sign_in', 'mcp-rovo'),
            unused('Linear', 'sign_in', 'mcp-linear'),
            unused('Atlassian Rovo', 'sign_in', 'mcp-rovo'),
          ]}
        />
      </Theme>,
    );
    expect(notes()[0]).toContain("Atlassian Rovo and Linear weren't used for this answer: sign in to them first.");
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Sign in to Atlassian Rovo',
      'Sign in to Linear',
    ]);
  });

  it('a sign-in the person cannot redo here points to their MCP servers instead', () => {
    render(<Theme><RunNotices parts={[unused('Wiki', 'sign_in')]} /></Theme>);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByRole('link', { name: 'Open MCP servers' }).getAttribute('href')).toBe('/workspace/mcp-servers/personal');
  });

  it('each reason has its own words; nothing approved links an admin to approve, and tells anyone else why', () => {
    const parts = [
      unused('Linear', 'not_approved'),
      unused('Wiki', 'unreachable'),
      unused('Docs', 'unreachable'),
      unused('Empty', 'no_tools'),
    ];
    render(<Theme><RunNotices parts={parts} /></Theme>);
    expect(notes()).toEqual([
      "Linear wasn't used for this answer: an admin hasn't approved any of its tools yet.",
      "Wiki and Docs weren't used for this answer: they didn't respond. Check them under MCP servers, then ask again. Open MCP servers",
      "Empty wasn't used for this answer: it offers no tools. Open MCP servers",
    ]);
    cleanup();
    admin.isAdmin = true;
    render(<Theme><RunNotices parts={parts} /></Theme>);
    expect(screen.getByRole('link', { name: 'Approve its tools' }).getAttribute('href')).toBe('/workspace/mcp-servers/team');
  });

  it("R27-SEAM: an admin's approve link opens the one server waiting on its hub tab; several open the list", () => {
    admin.isAdmin = true;
    render(<Theme><RunNotices parts={[unused('Linear', 'not_approved', 'inst-linear')]} /></Theme>);
    expect(screen.getByRole('link', { name: 'Approve its tools' }).getAttribute('href')).toBe(
      '/workspace/mcp-servers/team?serverId=inst-linear&tab=hub',
    );
    // An instance on a server waiting for approval is never a sign-in button.
    expect(screen.queryByRole('button')).toBeNull();
    cleanup();
    render(
      <Theme>
        <RunNotices parts={[unused('Linear', 'not_approved', 'inst-linear'), unused('Jira', 'not_approved', 'inst-jira')]} />
      </Theme>,
    );
    expect(screen.getByRole('link').getAttribute('href')).toBe('/workspace/mcp-servers/team');
  });

  it("an older Node's notice reads as it did; a server named without a reason still says which one", () => {
    render(
      <Theme>
        <RunNotices parts={[{ type: 'notice', code: 'mcp_unavailable' }, unused('Notes')]} />
      </Theme>,
    );
    expect(notes()).toEqual([
      "Notes wasn't used for this answer. Check it under MCP servers, then ask again. Open MCP servers",
      "Some of your MCP servers didn't respond or have no approved tools, so this answer couldn't use them. Check them under MCP servers, then try again.",
    ]);
  });

  it('the live frame keeps the server, its reason and its sign-in, once per server', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const rovo = { code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'sign_in', instance: 'mcp-rovo' };
    const linear = { code: 'mcp_unavailable', item: 'Linear', reason: 'not_approved' };
    for (const [id, value] of [['a', rovo], ['b', linear], ['c', rovo]] as const) {
      handle({ id, event: 'CUSTOM', data: { type: 'CUSTOM', name: 'notice', value, runId: 'run-1' } });
    }
    expect(parts.filter((part) => part.type === 'notice')).toEqual([
      { type: 'notice', ...rovo, runId: 'run-1' },
      { type: 'notice', ...linear, runId: 'run-1' },
    ]);
  });

  it('D-MCP-5: tools the description scanner held back are named with why, the server still used', () => {
    const held = (item: string, tools: string[]): MessagePart => ({ type: 'notice', code: 'mcp_tool_held', item, tools, runId: 'run-1' });
    render(
      <Theme>
        <RunNotices parts={[held('Atlassian Rovo', ['getConfluencePage']), held('Wiki', ['a', 'b']), held('Atlassian Rovo', ['getConfluencePage'])]} />
      </Theme>,
    );
    expect(notes()).toEqual([
      "Atlassian Rovo's tool getConfluencePage was held back for safety: its description looks like instructions to the model.",
      "Wiki's tools a and b were held back for safety: their descriptions look like instructions to the model.",
    ]);
  });

  it('D-MCP-5: a server whose every tool was flagged says so, with no approval or sign-in to offer', () => {
    render(<Theme><RunNotices parts={[unused('Wiki', 'flagged')]} /></Theme>);
    expect(notes()).toEqual([
      "Wiki wasn't used for this answer: its tools' descriptions look like instructions to the model, so they were held back for safety. Open MCP servers",
    ]);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
