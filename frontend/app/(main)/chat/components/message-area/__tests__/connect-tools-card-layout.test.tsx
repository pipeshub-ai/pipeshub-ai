/**
 * The look of the "tools not connected" notice: logos, a type line and one status column on every row, the two kinds
 * grouped, a long list folded, and the chip that opens it. Its states, strings and behaviour are the same as before
 * (connect-tools-card.test.tsx).
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { UnavailableTool } from '../../../unavailable-tools';

const toolsetsApi = vi.hoisted(() => ({ findMyToolsetByInstanceId: vi.fn(), getInstanceOAuthAuthorizationUrl: vi.fn() }));
const mcpApi = vi.hoisted(() => ({ getMyMcpServers: vi.fn(), getOAuthAuthorizationUrl: vi.fn(), authenticate: vi.fn() }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: toolsetsApi }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} /> }));
vi.mock('@/app/components/ui/ConnectorIcon', async (orig) => ({
  ...(await orig<typeof import('@/app/components/ui/ConnectorIcon')>()),
  ConnectorIcon: ({ type }: { type: string }) => <span data-logo={type} />,
}));
vi.mock('@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog', () => ({
  UserToolsetConfigDialog: ({ toolset, onSuccess }: { toolset: { displayName: string }; onSuccess: () => void }) => (
    <button type="button" onClick={onSuccess}>{`Save ${toolset.displayName}`}</button>
  ),
}));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: vi.fn(() => ({})) }));
vi.mock('../../../store', () => ({ useChatStore: { getState: () => ({ activeSlotId: 'slot-1' }) } }));

import { ConnectToolsCard } from '../connect-tools-card';
import { UnavailableToolsNotice } from '../unavailable-tools-notice';

const toolset = (id: string, name: string, over: Partial<UnavailableTool> = {}): UnavailableTool => ({
  kind: 'toolset', instanceId: id, displayName: name, name: name.toLowerCase(), problem: 'not_authenticated',
  sharedCredential: false, userCanConnect: true, ...over,
});
const mcp = (id: string, name: string, over: Partial<UnavailableTool> = {}): UnavailableTool => ({
  kind: 'mcp', instanceId: id, displayName: name, name: name.toLowerCase(), problem: 'not_authenticated', authMode: 'api_token',
  sharedCredential: false, userCanConnect: true, ...over,
});
const JIRA = toolset('ts-jira', 'Jira');
const GMAIL = toolset('ts-gmail', 'Gmail');
const SEARCH = mcp('mcp-search', 'Search', { sharedCredential: true, userCanConnect: false });
const OLD = mcp('mcp-old', 'Old Jira', { problem: 'not_found', userCanConnect: false });
const SLACK = toolset('ts-slack', 'Slack', { userCanConnect: false });

const rows = () => screen.getAllByTestId('connect-tools-row');
const card = (items: UnavailableTool[], blocked = false) =>
  renderInTheme(<ConnectToolsCard items={items} serviceAccount={false} question="Q" blocked={blocked} />);

beforeEach(() => installBrowserShims());
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the connect card’s rows', () => {
  it('gives every row its logo, its name with a tooltip, and its type under the name', () => {
    card([JIRA, SEARCH]);
    const [jira, search] = rows();
    expect(within(jira!).getByText('Jira').getAttribute('title')).toBe('Jira');
    expect(within(jira!).getByText('Action')).toBeTruthy();
    expect(jira!.querySelector('[data-logo]')).toBeTruthy();
    expect(within(search!).getByText('MCP server')).toBeTruthy();
    expect(search!.querySelector('[data-icon="hub"], [data-logo]')).toBeTruthy();
  });

  it('puts every Connect, Connected and status in one fixed column, on 40px rows with hairlines between them', async () => {
    toolsetsApi.findMyToolsetByInstanceId.mockResolvedValue({ name: 'jira', displayName: 'Jira', instanceId: 'ts-jira', authType: 'API_TOKEN' });
    card([JIRA, GMAIL, SEARCH, OLD, SLACK]);
    fireEvent.click(screen.getByRole('button', { name: 'Show 1 more' }));
    // Actions first, then MCP servers.
    const columns = screen.getAllByTestId('connect-tools-status');
    expect(columns).toHaveLength(5);
    const widths = new Set(columns.map((c) => c.style.width));
    expect(widths.size).toBe(1);
    expect([...widths][0]).not.toBe('');
    expect(within(columns[0]!).getByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(within(columns[1]!).getByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(within(columns[2]!).getByText('Ask an admin to connect it')).toBeTruthy();
    expect(within(columns[3]!).getByText('Waiting for an admin to add the shared credential')).toBeTruthy();
    expect(within(columns[4]!).getByText("Removed. Ask the agent's owner to update it.")).toBeTruthy();
    for (const row of rows()) expect(row.querySelector('[data-row-line]')!.getAttribute('style')).toContain('height: var(--space-7)');
    expect(rows()[0]!.style.borderTop).toBe('');
    expect(rows()[1]!.style.borderTop).toContain('var(--border-subtle)');

    await act(async () => {
      fireEvent.click(within(columns[0]!).getByRole('button', { name: 'Connect' }));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Jira' }));
    expect(within(screen.getAllByTestId('connect-tools-status')[0]!).getByText('Connected')).toBeTruthy();
  });

  it('a Connect is the small soft button, 28px tall', () => {
    card([JIRA]);
    const connect = screen.getByRole('button', { name: 'Connect' });
    expect(connect.className).toMatch(/rt-r-size-1/);
    expect(connect.className).toMatch(/rt-variant-soft/);
    expect(connect.style.height).toBe('calc(var(--space-5) + var(--space-1))');
  });

  it('groups actions and MCP servers under their names when both are there, and not when one kind is', () => {
    card([JIRA, SEARCH, GMAIL]);
    const labels = screen.getAllByTestId('connect-tools-group').map((g) => g.textContent);
    expect(labels).toEqual(['Actions', 'MCP servers']);
    expect(rows().map((r) => within(r).getByTestId('connect-tools-name').textContent)).toEqual(['Jira', 'Gmail', 'Search']);
    cleanup();
    card([JIRA, GMAIL]);
    expect(screen.queryAllByTestId('connect-tools-group')).toHaveLength(0);
  });

  it('folds rows past four behind Show N more, with no inner scroll', () => {
    const many = ['A', 'B', 'C', 'D', 'E', 'F'].map((n) => toolset(`ts-${n}`, `Tool ${n}`));
    card(many);
    expect(rows()).toHaveLength(4);
    const box = screen.getByTestId('connect-tools');
    expect(box.style.overflowY).not.toBe('auto');
    fireEvent.click(screen.getByRole('button', { name: 'Show 2 more' }));
    expect(rows()).toHaveLength(6);
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
    expect(rows()).toHaveLength(4);
  });

  it('heads the card with its title beside the icon in a small amber square', () => {
    card([JIRA]);
    const mark = screen.getByTestId('connect-tools-mark');
    expect(mark.style.background).toBe('var(--amber-a3)');
    expect(mark.querySelector('[data-icon="link_off"]')).toBeTruthy();
    expect(screen.getByTestId('connect-tools').style.border).toContain('var(--border-default)');
  });

  it('ends with a tinted footer strip, Try again as the solid primary on its right', async () => {
    toolsetsApi.findMyToolsetByInstanceId.mockResolvedValue({ name: 'jira', displayName: 'Jira', instanceId: 'ts-jira', authType: 'API_TOKEN' });
    card([JIRA], true);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Jira' }));
    const footer = screen.getByTestId('connect-tools-ready');
    expect(footer.style.borderTop).toContain('var(--border-subtle)');
    expect(footer.style.background).toBe('var(--emerald-a2)');
    const tryAgain = within(footer).getByRole('button', { name: 'Try again' });
    expect(tryAgain.className).toMatch(/rt-variant-solid/);
    expect(footer.lastElementChild).toBe(tryAgain);
    expect(within(footer).getByText('All connected. Send your message again to continue.')).toBeTruthy();
  });
});

describe('the chip and the read-only line', () => {
  it('the chip keeps its words, leads with up to three of the tools’ logos, and is a bordered quiet button', () => {
    renderInTheme(
      <UnavailableToolsNotice items={[JIRA, GMAIL, SLACK, toolset('ts-x', 'Extra')]} answer="Here is your summary." question="Q" />,
    );
    const chip = screen.getByTestId('tools-unavailable-chip');
    expect(chip.textContent).toContain("Some tools aren't connected");
    expect(chip.textContent).toContain('Connect');
    expect(chip.querySelectorAll('[data-logo]')).toHaveLength(3);
    expect(chip.className).toMatch(/rt-variant-outline/);
    expect(chip.style.height).toBe('calc(var(--space-5) + var(--space-1))');
    fireEvent.click(chip);
    expect(screen.getByTestId('connect-tools')).toBeTruthy();
  });

  it('the read-only line keeps its words, with the tools’ logos in front', () => {
    renderInTheme(
      <UnavailableToolsNotice items={[JIRA]} answer="x" question="Q" readOnlyFor={{ agentName: 'Helper' }} />,
    );
    const line = screen.getByTestId('tools-unavailable-line');
    expect(line.textContent).toContain("Helper couldn't use Jira");
    expect(line.querySelector('[data-logo]')).toBeTruthy();
    expect(line.querySelector('[data-icon="link_off"]')).toBeNull();
  });
});
