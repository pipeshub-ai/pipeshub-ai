import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import type { McpMyServerEntry, McpServerTemplate } from '../../../workspace/mcp-servers/types';

const mcpApi = vi.hoisted(() => ({ getCatalog: vi.fn(), getPopular: vi.fn(async () => []) }));
vi.mock('../../../workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
// The setup form has its own tests; here it only has to open with the right entry and report a save.
vi.mock('../../../workspace/mcp-servers/team/components/mcp-instance-config-panel', () => ({
  McpInstanceConfigPanel: (props: {
    scope: string;
    state: { prefillTemplate: McpServerTemplate | null };
    onSaved: (saved: { id: string; created: boolean }) => void;
  }) => (
    <div data-testid="setup-form">
      {props.state.prefillTemplate?.displayName} / {props.scope}
      <button type="button" onClick={() => props.onSaved({ id: 'new-1', created: true })}>
        Save it
      </button>
    </div>
  ),
}));

import { AgentBuilderMcpSection } from '../components/sidebar-mcp-section';

function template(overrides: Partial<McpServerTemplate>): McpServerTemplate {
  return {
    typeId: 'x',
    displayName: 'X',
    description: 'Does x.',
    transport: 'streamable_http',
    defaultAuthMode: 'oauth',
    supportedAuthModes: ['oauth'],
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    defaultScopes: [],
    supportsDcr: true,
    tags: [],
    ...overrides,
  };
}

const LINEAR = template({ typeId: 'linear', displayName: 'Linear', category: 'projects', capabilities: ['Create and update issues'] });
const GITHUB = template({ typeId: 'github', displayName: 'GitHub', category: 'developer', capabilities: ['Open and triage issues'] });
const LOCAL = template({ typeId: 'local_tool', displayName: 'Local tool', transport: 'stdio', command: 'npx' });

function server(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-gh',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'GitHub',
    typeId: 'github',
    transport: 'streamable_http',
    authMode: 'oauth',
    useAdminAuth: false,
    url: 'https://api.githubcopilot.com/mcp/',
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: [],
    isCustom: false,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [],
    ...overrides,
  };
}

function renderSection({
  servers = [server()],
  search = '',
  isAdmin = true,
  agentShared = false,
}: { servers?: McpMyServerEntry[]; search?: string; isAdmin?: boolean; agentShared?: boolean } = {}) {
  const refreshMcpServers = vi.fn(async () => {});
  const onNotify = vi.fn();
  const onServerAdded = vi.fn();
  renderInTheme(
    <AgentBuilderMcpSection
      mcpServers={servers}
      search={search}
      loading={false}
      refreshMcpServers={refreshMcpServers}
      mcpMergeCheckNodes={[]}
      isAdmin={isAdmin}
      agentShared={agentShared}
      onNotify={onNotify}
      onServerAdded={onServerAdded}
    />
  );
  return { refreshMcpServers, onNotify, onServerAdded };
}

beforeEach(() => {
  installBrowserShims();
  mcpApi.getCatalog.mockReset().mockImplementation(async (params?: { search?: string }) =>
    params?.search
      ? {
          templates: [
            { ...LINEAR, matchedCapability: 'Create and update issues' },
            { ...GITHUB, matchedCapability: 'Open and triage issues' },
          ],
          total: 2,
          customStdioAllowed: false,
        }
      : { templates: [LINEAR, GITHUB, LOCAL], total: 3, customStdioAllowed: true }
  );
});
afterEach(() => cleanup());

describe('Finding an MCP server from the agent builder', () => {
  it('offers the catalog instead of a dead end when nothing is added', () => {
    renderSection({ servers: [] });

    expect(screen.getByText(/No MCP servers yet/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Browse MCP servers' })).toBeTruthy();
    expect(mcpApi.getCatalog).not.toHaveBeenCalled();
  });

  it('answers a search no added server matches from the catalog, leaving out what is added', async () => {
    renderSection({ search: 'track issues' });

    expect(screen.getByText('None of your servers match.')).toBeTruthy();
    const fromCatalog = within(await screen.findByTestId('mcp-builder-from-catalog'));
    expect(fromCatalog.getByText('Linear')).toBeTruthy();
    expect(fromCatalog.getByText('Create and update issues')).toBeTruthy();
    expect(fromCatalog.queryByText('GitHub')).toBeNull();
  });

  it('adds a catalog server from its details, then refreshes the list and says to sign in', async () => {
    const { refreshMcpServers, onNotify, onServerAdded } = renderSection({ search: 'track issues' });

    fireEvent.click(within(await screen.findByTestId('mcp-builder-from-catalog')).getByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add to your organization' }));

    expect((await screen.findByTestId('setup-form')).textContent).toContain('Linear / org');
    fireEvent.click(screen.getByRole('button', { name: 'Save it' }));

    await waitFor(() => expect(refreshMcpServers).toHaveBeenCalled());
    expect(onNotify).toHaveBeenCalledWith('Linear is added. Sign in to it from the list to use its tools.');
    expect(onServerAdded).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('setup-form')).toBeNull();
  });

  it('adds a member’s own server for a personal agent', async () => {
    renderSection({ search: 'track issues', isAdmin: false });

    fireEvent.click(within(await screen.findByTestId('mcp-builder-from-catalog')).getByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add for yourself' }));

    expect((await screen.findByTestId('setup-form')).textContent).toContain('Linear / personal');
  });

  it('sends a member building a shared agent to an admin instead', async () => {
    renderSection({ search: 'track issues', isAdmin: false, agentShared: true });

    const fromCatalog = within(await screen.findByTestId('mcp-builder-from-catalog'));
    expect(fromCatalog.getByText(/Ask an admin to add one/)).toBeTruthy();
    fireEvent.click(fromCatalog.getByRole('button', { name: 'View' }));

    const details = within(await screen.findByTestId('mcp-catalog-details'));
    expect(details.getByText(/Ask an admin to add Linear for your organization/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Add for yourself|Add to your organization/ })).toBeNull();
  });

  it('browses the catalog over the canvas, with what this person may add', async () => {
    renderSection({ isAdmin: false });

    fireEvent.click(screen.getByRole('button', { name: 'Browse MCP servers' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('Browse MCP servers')).toBeTruthy();
    await waitFor(() => expect(dialog.getAllByTestId('mcp-catalog-card')).toHaveLength(2));
    // A local command runs on PipesHub's machine, so a member can't add one.
    expect(dialog.queryByText('Local tool')).toBeNull();

    const linear = dialog.getAllByTestId('mcp-catalog-card').find((card) => within(card).queryByText('Linear'))!;
    fireEvent.click(within(linear).getByRole('button', { name: /Set up/ }));
    expect((await screen.findByTestId('setup-form')).textContent).toContain('Linear / personal');
    expect(screen.queryByRole('dialog', { name: 'Browse MCP servers' })).toBeNull();
  });

  it('steps out of Browse to show a server’s details, and goes back when they close', async () => {
    renderSection();
    fireEvent.click(screen.getByRole('button', { name: 'Browse MCP servers' }));
    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() => expect(dialog.getAllByTestId('mcp-catalog-card')).toHaveLength(3));

    const linear = dialog.getAllByTestId('mcp-catalog-card').find((card) => within(card).queryByText('Linear'))!;
    fireEvent.click(within(linear).getByRole('link', { name: 'Linear' }));

    await screen.findByTestId('mcp-catalog-details');
    // Browse is modal; left open it would keep the keyboard out of the details.
    expect(screen.queryByRole('dialog', { name: 'Browse MCP servers' })).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' }).at(-1)!);

    expect(await screen.findByRole('dialog', { name: 'Browse MCP servers' })).toBeTruthy();
    expect(screen.queryByTestId('mcp-catalog-details')).toBeNull();
  });

  it('says when the catalog can’t load and asks again', async () => {
    mcpApi.getCatalog.mockRejectedValueOnce(new Error('down'));
    renderSection();

    fireEvent.click(screen.getByRole('button', { name: 'Browse MCP servers' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByText("Couldn't load the catalog.")).toBeTruthy();

    fireEvent.click(dialog.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(dialog.getAllByTestId('mcp-catalog-card')).toHaveLength(3));
    expect(mcpApi.getCatalog).toHaveBeenCalledTimes(2);
  });
});
