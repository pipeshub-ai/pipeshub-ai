import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { ErrorType } from '@/lib/api/api-error';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { toast } from '@/lib/store/toast-store';
import type { McpMyServerEntry, McpServerTemplate } from '../types';

const mcpApi = vi.hoisted(() => ({
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  discoverOAuthMetadata: vi.fn(),
  getOAuthConfig: vi.fn(),
  authenticate: vi.fn(),
  updateOAuthConfig: vi.fn(),
  getInstanceTools: vi.fn(),
  getToolPolicy: vi.fn(),
  updateToolPolicy: vi.fn(),
  getMyToolRules: vi.fn(),
  updateMyToolRules: vi.fn(),
}));
vi.mock('../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { McpPersonalLayout } from '../personal/components/mcp-personal-layout';
import { McpPersonalServerCard } from '../personal/components/mcp-personal-server-card';
import { McpAddServerDialog } from '../personal/components/mcp-add-server-dialog';
import { McpInstanceConfigPanel } from '../team/components/mcp-instance-config-panel';
import { McpUserCreatedSection } from '../team/components/mcp-user-created-section';
import { McpDisconnectDialog } from '../components/mcp-disconnect-dialog';
import type { McpToolInfo } from '../types';

function tool(name: string): McpToolInfo {
  return { name, namespacedName: `mcp_x_${name}`, inputSchema: {} };
}

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'Server',
    transport: 'streamable_http',
    authMode: 'none',
    useAdminAuth: false,
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: [],
    isCustom: true,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: false,
    tools: [],
    ...overrides,
  };
}

function template(overrides: Partial<McpServerTemplate> = {}): McpServerTemplate {
  return {
    typeId: 'remote',
    displayName: 'Remote One',
    description: 'A remote server',
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

const noop = () => {};

beforeEach(() => {
  installBrowserShims();
  mcpApi.createInstance.mockResolvedValue({ _id: 'new-1' });
  mcpApi.discoverOAuthMetadata.mockResolvedValue({ supportsDcr: false, metadataFound: false, scopesSupported: [] });
  mcpApi.getOAuthConfig.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('McpPersonalLayout', () => {
  function layout(props: Partial<React.ComponentProps<typeof McpPersonalLayout>> = {}) {
    renderInTheme(
      <McpPersonalLayout
        instances={[]}
        templates={[]}
        isLoading={false}
        searchQuery=""
        busyInstanceId={null}
        onSearchChange={noop}
        onRefresh={noop}
        onAddServer={noop}
        onSetUp={noop}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onRemoveCredentials={noop}
        onToolApprovals={noop}
        onEdit={noop}
        onDelete={noop}
        {...props}
      />,
    );
  }

  const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

  it('shows every server and the catalog entries nobody has set up as cards, like the toolsets page', () => {
    const onSetUp = vi.fn();
    const notion = template({ typeId: 'notion', displayName: 'Notion', description: 'Pages and databases' });
    layout({
      instances: [
        entry({ _id: 'org', name: 'Team Server', typeId: 'github' }),
        entry({ _id: 'mine', name: 'My Server', scope: 'personal' }),
        entry({ _id: 'off', name: 'Needs Sign-in', authMode: 'oauth', isAuthenticated: false }),
      ],
      templates: [
        notion,
        template({ typeId: 'github', displayName: 'GitHub' }),
        template({ typeId: 'local', displayName: 'Local Files', transport: 'stdio' }),
        template({ typeId: 'old', displayName: 'Old Slack', replacedBy: 'slack' }),
      ],
      onSetUp,
    });

    // Connected first, my own before the organization's; then the rest; then what can be set up.
    const mine = screen.getByText('My Server');
    const team = screen.getByText('Team Server');
    const off = screen.getByText('Needs Sign-in');
    const offer = screen.getByText('Notion');
    expect(follows(mine, team) && follows(team, off) && follows(off, offer)).toBe(true);
    expect(screen.getByText('Yours')).toBeTruthy();
    // Already set up here, a local command, or replaced: not offered.
    expect(screen.queryByText('GitHub')).toBeNull();
    expect(screen.queryByText('Local Files')).toBeNull();
    expect(screen.queryByText('Old Slack')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Setup' }));
    expect(onSetUp).toHaveBeenCalledWith(notion);
  });

  it('filters by tab, with counts', () => {
    layout({
      instances: [entry({ _id: 'ready', name: 'Ready One' }), entry({ _id: 'off', name: 'Off One', authMode: 'oauth' })],
      templates: [template({ typeId: 'notion', displayName: 'Notion' })],
    });

    expect(screen.getByRole('radio', { name: 'All (3)' })).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'Connected (1)' }));
    expect(screen.getByText('Ready One')).toBeTruthy();
    expect(screen.queryByText('Off One')).toBeNull();
    expect(screen.queryByText('Notion')).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: 'Not connected (2)' }));
    expect(screen.queryByText('Ready One')).toBeNull();
    expect(screen.getByText('Off One')).toBeTruthy();
    expect(screen.getByText('Notion')).toBeTruthy();
  });

  it('searches servers and catalog entries alike', () => {
    layout({
      instances: [entry({ name: 'Team Server' })],
      templates: [template({ typeId: 'notion', displayName: 'Notion' })],
      searchQuery: 'notion',
    });

    expect(screen.queryByText('Team Server')).toBeNull();
    expect(screen.getByText('Notion')).toBeTruthy();
  });

  it('says what to do when there is nothing at all', () => {
    layout();
    expect(screen.getByText('No MCP servers available')).toBeTruthy();
    expect(screen.getByText('Add one of your own, or ask your admin to add one for everyone.')).toBeTruthy();
  });
});

describe('McpDisconnectDialog', () => {
  it('says what stops, what is kept and what is deleted, then disconnects', () => {
    const onConfirm = vi.fn();
    renderInTheme(
      <McpDisconnectDialog
        instance={entry({ name: 'Jira', authMode: 'oauth', isAuthenticated: true, tools: [tool('a'), tool('b')] })}
        onOpenChange={noop}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByText('Disconnect Jira?')).toBeTruthy();
    expect(screen.getByText("You'll stop using its 2 tools until you sign in again.")).toBeTruthy();
    expect(screen.getByText('Server settings and approval rules are kept')).toBeTruthy();
    expect(screen.getByText('Your saved sign-in is deleted')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it('warns that a shared credential stops the server for everyone', () => {
    renderInTheme(
      <McpDisconnectDialog
        instance={entry({ name: 'Jira', authMode: 'api_token', useAdminAuth: true, isAuthenticated: true })}
        onOpenChange={noop}
        onConfirm={noop}
      />,
    );

    expect(
      screen.getByText('Agents in your organization will stop using its tools right away, including in Slack and scheduled runs.')
    ).toBeTruthy();
    expect(screen.getByText('The shared sign-in is deleted')).toBeTruthy();
  });
});

describe('McpPersonalServerCard', () => {
  it('offers edit and delete on my own server', async () => {
    const onEdit = vi.fn();
    renderInTheme(
      <McpPersonalServerCard
        instance={entry({ scope: 'personal' })}
        isBusy={false}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onRemoveCredentials={noop}
        onEdit={onEdit}
        onDelete={noop}
      />,
    );

    fireEvent.click(screen.getByRole('button'));
    expect(await screen.findByText('Delete')).toBeTruthy();
    fireEvent.click(screen.getByText('Edit'));
    expect(onEdit).toHaveBeenCalledOnce();
  });

  it('offers my own tool approvals on any server', async () => {
    const onToolApprovals = vi.fn();
    renderInTheme(
      <McpPersonalServerCard
        instance={entry()}
        isBusy={false}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onRemoveCredentials={noop}
        onToolApprovals={onToolApprovals}
      />,
    );

    fireEvent.click(screen.getByRole('button'));
    fireEvent.click(await screen.findByText('Tool approvals'));
    expect(onToolApprovals).toHaveBeenCalledOnce();
  });

  it('has no menu on an organization server that needs nothing from me', () => {
    renderInTheme(
      <McpPersonalServerCard
        instance={entry()}
        isBusy={false}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onRemoveCredentials={noop}
      />,
    );

    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('McpUserCreatedSection', () => {
  it('names the owner and transport, never where the server is', () => {
    const summary = { ...entry({ scope: 'personal', name: 'Zap', createdBy: 'u-9' }), url: 'https://mcp.zapier.com/s/SECRET/mcp' };
    renderInTheme(<McpUserCreatedSection instances={[summary]} ownerNames={{ 'u-9': 'Ada' }} onDelete={noop} />);

    expect(screen.getByText('Zap')).toBeTruthy();
    expect(screen.getByText(/Added by Ada/)).toBeTruthy();
    expect(document.body.textContent).not.toContain('SECRET');
  });
});

describe('McpAddServerDialog', () => {
  it('offers remote catalog servers and a custom one, never a local command', () => {
    const onPickCustom = vi.fn();
    renderInTheme(
      <McpAddServerDialog
        open
        templates={[template(), template({ typeId: 'local', displayName: 'Local One', transport: 'stdio' })]}
        onOpenChange={noop}
        onPickTemplate={noop}
        onPickCustom={onPickCustom}
      />,
    );

    expect(screen.getByText('Remote One')).toBeTruthy();
    expect(screen.queryByText('Local One')).toBeNull();
    fireEvent.click(screen.getByText('Custom server'));
    expect(onPickCustom).toHaveBeenCalledOnce();
  });
});

describe('McpInstanceConfigPanel scope', () => {
  function renderPanel(scope: 'org' | 'personal', customStdioAllowed = true) {
    renderInTheme(
      <McpInstanceConfigPanel
        scope={scope}
        customStdioAllowed={customStdioAllowed}
        state={{ open: true, mode: 'create', editingInstance: null, prefillTemplate: null }}
        templates={[]}
        instances={[]}
        onOpenChange={noop}
        onSaved={noop}
        onRequestDelete={noop}
        busyInstanceId={null}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onDisconnect={noop}
      />,
    );
  }

  function choose(currentLabel: string, optionLabel: string) {
    fireEvent.click(screen.getByText(currentLabel));
    fireEvent.click(screen.getAllByText(optionLabel).at(-1)!);
  }

  it('creates a personal server without a local command or a shared admin credential', async () => {
    renderPanel('personal');

    fireEvent.click(screen.getByText('Streamable HTTP'));
    expect(screen.queryByText('STDIO (command)')).toBeNull();
    fireEvent.click(screen.getAllByText('Streamable HTTP').at(-1)!);

    choose('No authentication', 'API token');
    expect(screen.queryByText('Use shared admin authentication for all users')).toBeNull();

    const [nameInput] = screen.getAllByRole('textbox');
    fireEvent.change(nameInput, { target: { value: 'Mine' } });
    fireEvent.change(screen.getByPlaceholderText('https://example.com/mcp'), {
      target: { value: 'https://mcp.example.com/mcp' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mcpApi.createInstance).toHaveBeenCalledOnce());
    expect(mcpApi.createInstance.mock.calls[0][0]).toMatchObject({
      scope: 'personal',
      transport: 'streamable_http',
      authMode: 'api_token',
      useAdminAuth: false,
      url: 'https://mcp.example.com/mcp',
    });
  });

  it('sends the timeouts it was given and refuses ones out of range', async () => {
    renderPanel('personal');

    const [nameInput] = screen.getAllByRole('textbox');
    fireEvent.change(nameInput, { target: { value: 'Mine' } });
    fireEvent.change(screen.getByPlaceholderText('https://example.com/mcp'), {
      target: { value: 'https://mcp.example.com/mcp' },
    });
    const connect = screen.getByPlaceholderText('15');
    fireEvent.change(connect, { target: { value: '500' } });
    expect(screen.getByText('Enter a number from 1 to 45, or leave it blank for the default.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(connect, { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mcpApi.createInstance).toHaveBeenCalledOnce());
    expect(mcpApi.createInstance.mock.calls[0][0]).toMatchObject({ connectTimeoutSeconds: 30, callTimeoutSeconds: null });
  });

  it('offers no local command for a custom server when the deployment forbids it', () => {
    renderPanel('org', false);

    fireEvent.click(screen.getByText('Streamable HTTP'));
    expect(screen.queryByText('STDIO (command)')).toBeNull();
  });

  it('keeps the administrator options for an organization server', () => {
    renderPanel('org');

    expect(screen.getByText('STDIO (command)')).toBeTruthy();
    choose('No authentication', 'API token');
    expect(screen.getByText('Use shared admin authentication for all users')).toBeTruthy();
  });
});

describe('McpInstanceConfigPanel — Tools & approvals', () => {
  const SEARCH = { name: 'search', namespacedName: 'mcp_server_search', inputSchema: {}, kind: 'read' as const, kindSource: 'server' as const };
  const CREATE = { name: 'create_issue', namespacedName: 'mcp_server_create_issue', inputSchema: {}, kind: 'write' as const, kindSource: 'none' as const };
  const DELETE = { name: 'delete_issue', namespacedName: 'mcp_server_delete_issue', inputSchema: {}, kind: 'destructive' as const, kindSource: 'name' as const };

  function renderEditing(editing: McpMyServerEntry, props: Partial<React.ComponentProps<typeof McpInstanceConfigPanel>> = {}) {
    const handlers = {
      onOpenChange: vi.fn(),
      onRequestDelete: vi.fn(),
      onDisconnect: vi.fn(),
      onReauthenticate: vi.fn(),
    };
    renderInTheme(
      <McpInstanceConfigPanel
        scope="org"
        state={{ open: true, mode: 'edit', editingInstance: editing, prefillTemplate: null }}
        templates={[]}
        instances={[editing]}
        customStdioAllowed={false}
        onSaved={noop}
        busyInstanceId={null}
        onAuthenticate={noop}
        {...handlers}
        {...props}
      />,
    );
    return handlers;
  }

  function openTools() {
    fireEvent.mouseDown(screen.getByRole('tab', { name: /^Tools & approvals/ }));
  }

  function apiError(statusCode: number, message: string) {
    return { type: ErrorType.SERVER_ERROR, message, statusCode };
  }

  beforeEach(() => {
    mcpApi.getToolPolicy.mockResolvedValue({ tools: {} });
    mcpApi.getMyToolRules.mockResolvedValue({ tools: {} });
    mcpApi.updateToolPolicy.mockResolvedValue({ tools: {} });
    mcpApi.getInstanceTools.mockResolvedValue({ tools: [SEARCH, CREATE, DELETE], syncedAt: Date.now() - 120_000 });
  });

  it('reads the cached tool list when the tab opens and shows the company rules inline, grouped', async () => {
    renderEditing(entry({ createdBy: '' }));
    openTools();

    expect(await screen.findByRole('group', { name: 'delete_issue' })).toBeTruthy();
    expect(mcpApi.getInstanceTools).toHaveBeenCalledWith('inst-1', { cached: true });
    expect(mcpApi.getToolPolicy).toHaveBeenCalledWith('inst-1');
    expect(screen.getByText('Rules here apply to everyone in your organization. Members can add stricter rules, never looser ones.')).toBeTruthy();
    expect(screen.getByText(/^Synced /)).toBeTruthy();
    expect(screen.getByText('All rules saved')).toBeTruthy();
    const groups = screen.getAllByTestId(/^tool-group-/).map((group) => group.dataset.testid);
    expect(groups).toEqual(['tool-group-destructive', 'tool-group-write', 'tool-group-read']);
  });

  it('counts unsaved changes, discards them, and saves the company rules', async () => {
    renderEditing(entry({ createdBy: '' }));
    openTools();

    const create = within(await screen.findByRole('group', { name: 'create_issue' }));
    fireEvent.click(create.getByRole('radio', { name: 'Allow on approval' }));
    expect(screen.getByText('1 unsaved change')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.getByText('All rules saved')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(screen.getByText('2 unsaved changes')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }));

    await waitFor(() =>
      expect(mcpApi.updateToolPolicy).toHaveBeenCalledWith('inst-1', {
        tools: { create_issue: { rule: 'ask', unattended: false }, delete_issue: { rule: 'ask', unattended: false } },
      })
    );
    expect(await screen.findByText('All rules saved')).toBeTruthy();
  });

  it('asks before closing with unsaved rule changes', async () => {
    const handlers = renderEditing(entry({ createdBy: '' }));
    openTools();
    fireEvent.click(within(await screen.findByRole('group', { name: 'create_issue' })).getByRole('radio', { name: 'Deny' }));

    fireEvent.mouseDown(screen.getByRole('tab', { name: /^Configuration/ }));
    openTools();
    // Kept across tabs.
    expect(screen.getByText('1 unsaved change')).toBeTruthy();

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    expect(await screen.findByText('Discard unsaved rule changes?')).toBeTruthy();
    expect(handlers.onOpenChange).not.toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(handlers.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('refreshes live', async () => {
    renderEditing(entry({ createdBy: '' }));
    openTools();
    await screen.findByRole('group', { name: 'search' });

    fireEvent.click(screen.getByRole('button', { name: 'Refresh the tool list' }));

    await waitFor(() => expect(mcpApi.getInstanceTools).toHaveBeenLastCalledWith('inst-1', { cached: false }));
  });

  it("says when the list couldn't load, keeps the rules, and tries again", async () => {
    mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(502, "Couldn't load this MCP server's tools."));
    renderEditing(entry({ createdBy: '' }));
    openTools();

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText("Couldn't load the tool list")).toBeTruthy();
    expect(within(alert).getByText(/Your saved approval rules are kept\./)).toBeTruthy();
    expect(within(screen.getByTestId('mcp-panel-status')).getByText("Can't reach server")).toBeTruthy();

    fireEvent.click(within(alert).getByRole('button', { name: /Try again/ }));
    expect(await screen.findByRole('group', { name: 'search' })).toBeTruthy();
  });

  it('shows an expired sign-in with Reauthenticate when the server refuses the sign-in', async () => {
    mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(409, 'Reconnect needed'));
    const handlers = renderEditing(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true }));
    openTools();

    expect(await screen.findByText('Sign-in expired')).toBeTruthy();
    expect(within(screen.getByTestId('mcp-panel-status')).getByText('Reconnect needed')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Reauthenticate' })[0]);
    expect(handlers.onReauthenticate).toHaveBeenCalled();
  });

  it('lists the tools again once someone signs in again', async () => {
    mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(409, 'Reconnect needed'));
    const editing = entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true, connectedAt: 1 });
    const panel = (instance: McpMyServerEntry) => (
      <Theme>
        <McpInstanceConfigPanel
          scope="org"
          state={{ open: true, mode: 'edit', editingInstance: editing, prefillTemplate: null }}
          templates={[]}
          instances={[instance]}
          customStdioAllowed={false}
          onOpenChange={noop}
          onSaved={noop}
          onRequestDelete={noop}
          busyInstanceId={null}
          onAuthenticate={noop}
          onReauthenticate={noop}
          onDisconnect={noop}
        />
      </Theme>
    );
    const { rerender } = render(panel(editing));
    openTools();
    expect(await screen.findByText('Sign-in expired')).toBeTruthy();

    rerender(panel({ ...editing, connectedAt: 2 }));

    expect(await screen.findByRole('group', { name: 'search' })).toBeTruthy();
    expect(screen.queryByText('Sign-in expired')).toBeNull();
  });

  it("edits a personal server's own rules, not the company's", async () => {
    renderEditing(entry({ createdBy: '', scope: 'personal' }), { scope: 'personal' });
    openTools();

    const search = within(await screen.findByRole('group', { name: 'search' }));
    expect(search.getByRole('radio', { name: 'Pre-approved', checked: true })).toBeTruthy();
    expect(mcpApi.getMyToolRules).toHaveBeenCalledWith('inst-1');
    expect(mcpApi.getToolPolicy).not.toHaveBeenCalled();
  });

  it("never shows one server's list in another server's panel", async () => {
    let answerA: (value: unknown) => void = () => {};
    mcpApi.getInstanceTools.mockImplementation((id: string) =>
      id === 'inst-a'
        ? new Promise((resolve) => {
            answerA = resolve;
          })
        : Promise.resolve({ tools: [CREATE], syncedAt: Date.now() })
    );
    const a = entry({ _id: 'inst-a', name: 'Server A', createdBy: '' });
    const b = entry({ _id: 'inst-b', name: 'Server B', createdBy: '' });
    const panel = (editing: McpMyServerEntry, open: boolean) => (
      <Theme>
        <McpInstanceConfigPanel
          scope="org"
          state={{ open, mode: 'edit', editingInstance: editing, prefillTemplate: null }}
          templates={[]}
          instances={[a, b]}
          customStdioAllowed={false}
          onOpenChange={noop}
          onSaved={noop}
          onRequestDelete={noop}
          busyInstanceId={null}
          onAuthenticate={noop}
          onReauthenticate={noop}
          onDisconnect={noop}
        />
      </Theme>
    );
    const { rerender } = render(panel(a, true));
    openTools();
    await waitFor(() => expect(mcpApi.getInstanceTools).toHaveBeenCalledWith('inst-a', { cached: true }));

    rerender(panel(a, false));
    rerender(panel(b, true));
    openTools();
    expect(await screen.findByRole('group', { name: 'create_issue' })).toBeTruthy();

    answerA({ tools: [SEARCH, DELETE], syncedAt: Date.now() });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole('group', { name: 'search' })).toBeNull();
    expect(screen.getByRole('group', { name: 'create_issue' })).toBeTruthy();
  });

  it('asks about unsaved rule changes when the configuration is saved', async () => {
    const editing = entry({ createdBy: '', url: 'https://mcp.example.com/mcp' });
    mcpApi.updateInstance.mockResolvedValue({ ...editing });
    const handlers = renderEditing(editing);
    openTools();
    fireEvent.click(within(await screen.findByRole('group', { name: 'create_issue' })).getByRole('radio', { name: 'Deny' }));

    fireEvent.mouseDown(screen.getByRole('tab', { name: /^Configuration/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Discard unsaved rule changes?')).toBeTruthy();
    expect(mcpApi.updateInstance).toHaveBeenCalled();
    expect(handlers.onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('Escape closes the discard prompt, not the panel behind it', async () => {
    const handlers = renderEditing(entry({ createdBy: '' }));
    openTools();
    fireEvent.click(within(await screen.findByRole('group', { name: 'create_issue' })).getByRole('radio', { name: 'Deny' }));
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(await screen.findByText('Discard unsaved rule changes?')).toBeTruthy();

    fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' });

    await waitFor(() => expect(screen.queryByText('Discard unsaved rule changes?')).toBeNull());
    expect(handlers.onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByText('1 unsaved change')).toBeTruthy();
  });

  it('sets company rules by name before anyone has signed in', async () => {
    mcpApi.getToolPolicy.mockResolvedValue({ tools: { delete_repo: { rule: 'block', unattended: false } } });
    renderEditing(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: false }));
    openTools();

    expect(await screen.findByText("Sign in to see this server's tools.")).toBeTruthy();
    expect(await screen.findByRole('group', { name: 'delete_repo' })).toBeTruthy();
    expect(screen.getByText(/Rules set by name/)).toBeTruthy();
    expect(screen.queryByText('Not offered now')).toBeNull();
    fireEvent.change(screen.getByPlaceholderText('Add a tool by name'), { target: { value: 'push_code' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(within(screen.getByRole('group', { name: 'push_code' })).getByRole('radio', { name: 'Deny' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save rules' }));

    await waitFor(() =>
      expect(mcpApi.updateToolPolicy).toHaveBeenCalledWith('inst-1', {
        tools: { delete_repo: { rule: 'block', unattended: false }, push_code: { rule: 'block', unattended: false } },
      })
    );
    expect(mcpApi.getInstanceTools).not.toHaveBeenCalled();
  });

  it('asks to sign in before listing tools', async () => {
    renderEditing(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: false }));
    openTools();

    expect(await screen.findByText("Sign in to see this server's tools.")).toBeTruthy();
    expect(mcpApi.getInstanceTools).not.toHaveBeenCalled();
  });
});

describe('McpInstanceConfigPanel — More actions', () => {
  function renderEditing(editing: McpMyServerEntry) {
    const handlers = { onRequestDelete: vi.fn(), onDisconnect: vi.fn(), onReauthenticate: vi.fn() };
    renderInTheme(
      <McpInstanceConfigPanel
        scope="org"
        state={{ open: true, mode: 'edit', editingInstance: editing, prefillTemplate: null }}
        templates={[]}
        instances={[editing]}
        customStdioAllowed={false}
        onOpenChange={noop}
        onSaved={noop}
        busyInstanceId={null}
        onAuthenticate={noop}
        {...handlers}
      />,
    );
    return handlers;
  }

  function openMenu() {
    fireEvent.keyDown(screen.getByRole('button', { name: 'More actions' }), { key: 'Enter' });
  }

  it('disconnects only after asking', async () => {
    const editing = entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true, url: 'https://mcp.example.com/mcp' });
    const handlers = renderEditing(editing);
    openMenu();

    fireEvent.click(await screen.findByRole('menuitem', { name: /Disconnect/ }));
    expect(handlers.onDisconnect).not.toHaveBeenCalled();
    expect(await screen.findByText('Disconnect Server?')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(handlers.onDisconnect).toHaveBeenCalledWith(editing);
  });

  it('removes the server through the page', async () => {
    const editing = entry({ createdBy: '' });
    const handlers = renderEditing(editing);
    openMenu();

    fireEvent.click(await screen.findByRole('menuitem', { name: /Remove server/ }));
    expect(handlers.onRequestDelete).toHaveBeenCalledWith(editing);
  });

  it('copies the server URL', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderEditing(entry({ createdBy: '', url: 'https://mcp.example.com/mcp' }));
    openMenu();

    fireEvent.click(await screen.findByRole('menuitem', { name: /Copy server URL/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('https://mcp.example.com/mcp'));
  });
});

describe('McpInstanceConfigPanel edit', () => {
  it('warns when the save removed stored credentials', async () => {
    const editing = entry({ url: 'https://mcp.example.com/mcp' });
    mcpApi.updateInstance.mockResolvedValue({ ...editing, credentialsReset: true });
    const warning = vi.spyOn(toast, 'warning');
    const success = vi.spyOn(toast, 'success');
    renderInTheme(
      <McpInstanceConfigPanel
        scope="org"
        customStdioAllowed
        state={{ open: true, mode: 'edit', editingInstance: editing, prefillTemplate: null }}
        templates={[]}
        instances={[editing]}
        onOpenChange={noop}
        onSaved={noop}
        onRequestDelete={noop}
        busyInstanceId={null}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onDisconnect={noop}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(warning).toHaveBeenCalledOnce());
    expect(warning.mock.calls[0][0]).toMatch(/Saved credentials were removed/);
    expect(success).not.toHaveBeenCalled();
  });
});
