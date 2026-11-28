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
  getMyToolRules: vi.fn(),
  updateMyToolRules: vi.fn(),
  getPopular: vi.fn(async () => []),
}));
// These tests cover the default notices, whatever the build's config provides.
// The Enterprise notices these screens show are for admins; render them as an admin would see them.
vi.mock('@/lib/hooks/use-enterprise-prompt', () => ({ useEnterprisePrompt: () => true }));

vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  EnterpriseFeatureNotice: (await import('@/app/components/ui/enterprise-badge')).EnterpriseFeatureNotice,
  McpAgentApprovalsNotice: (await import('@/app/(main)/agents/agent-builder/components/mcp-agent-approvals-notice'))
    .McpAgentApprovalsNotice,
}));
vi.mock('../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { McpServersLayout } from '../components/mcp-servers-layout';
import { McpServerPage } from '../components/mcp-server-page';
import { offeredForPersonalServers } from '../catalog-replacement';
import { McpAddServerDialog } from '../personal/components/mcp-add-server-dialog';
import { McpInstanceConfigPanel } from '../team/components/mcp-instance-config-panel';
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

describe('McpServersLayout, as on the personal page', () => {
  function layout(props: Partial<React.ComponentProps<typeof McpServersLayout>> = {}) {
    const templates = props.templates ?? [];
    renderInTheme(
      <McpServersLayout
        title="MCP servers"
        subtitle=""
        actions={null}
        isLoading={false}
        searchQuery=""
        onSearchChange={noop}
        instances={[]}
        templates={templates}
        catalog={offeredForPersonalServers(templates)}
        catalogDescription="Servers you add here are only visible to you."
        emptyTitle="No MCP servers available"
        emptyDescription="Add one of your own, or ask your admin to add one for everyone."
        isAdmin={false}
        busyInstanceId={null}
        onOpen={noop}
        onConnect={noop}
        onReconnect={noop}
        onAdd={noop}
        {...props}
      />,
    );
  }

  const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

  it('lists every server with its status, those that need something first, then what can be added', () => {
    const onAdd = vi.fn();
    const notion = template({ typeId: 'notion', displayName: 'Notion', description: 'Pages and databases' });
    const github = template({ typeId: 'github', displayName: 'GitHub' });
    layout({
      instances: [
        entry({ _id: 'org', name: 'Team Server', typeId: 'github', isCustom: false }),
        entry({ _id: 'mine', name: 'My Server', scope: 'personal' }),
        entry({ _id: 'off', name: 'Needs Sign-in', authMode: 'oauth', isAuthenticated: false }),
      ],
      templates: [
        notion,
        github,
        template({ typeId: 'local', displayName: 'Local Files', transport: 'stdio' }),
        template({ typeId: 'old', displayName: 'Old Slack', replacedBy: 'slack' }),
      ],
      onAdd,
    });

    const off = screen.getByText('Needs Sign-in');
    const mine = screen.getByText('My Server');
    const team = screen.getByText('Team Server');
    const offer = screen.getByText('Notion');
    expect(follows(off, mine) && follows(mine, team) && follows(team, offer)).toBe(true);
    expect(screen.getByText('Yours')).toBeTruthy();
    expect(screen.getAllByText('Connected')).toHaveLength(2);
    expect(screen.getByText('Not connected')).toBeTruthy();
    // A local command or a replaced entry is not offered; one already added can be added again.
    expect(screen.queryByText('Local Files')).toBeNull();
    expect(screen.queryByText('Old Slack')).toBeNull();
    expect(screen.getByText('Added')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Set up' }));
    expect(onAdd).toHaveBeenCalledWith(notion);
    fireEvent.click(screen.getByRole('button', { name: 'Add another' }));
    expect(onAdd).toHaveBeenCalledWith(github);
  });

  it('counts each status and filters by it', () => {
    layout({
      instances: [
        entry({ _id: 'ready', name: 'Ready One' }),
        entry({ _id: 'off', name: 'Off One', authMode: 'oauth' }),
        entry({ _id: 'down', name: 'Down One', toolsErrorCode: 'unreachable', toolsError: 'no answer' }),
      ],
    });

    expect(screen.getByRole('radio', { name: 'All (3)' })).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'Connected (1)' }));
    expect(screen.getByText('Ready One')).toBeTruthy();
    expect(screen.queryByText('Off One')).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: 'Needs action (1)' }));
    expect(screen.getByText('Off One')).toBeTruthy();
    expect(screen.queryByText('Ready One')).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: 'Unavailable (1)' }));
    expect(screen.getByText('Down One')).toBeTruthy();
    expect(screen.queryByText('Off One')).toBeNull();
  });

  it('offers the one fix on the row, and opens the server from anywhere else on it', () => {
    const onConnect = vi.fn();
    const onReconnect = vi.fn();
    const onOpen = vi.fn();
    const expired = entry({ _id: 'exp', name: 'Expired', authMode: 'oauth', isAuthenticated: true, toolsErrorCode: 'auth_expired' });
    const fresh = entry({ _id: 'new', name: 'Fresh', authMode: 'oauth' });
    const waiting = entry({ _id: 'wait', name: 'Waiting', authMode: 'api_token', useAdminAuth: true });
    layout({ instances: [expired, fresh, waiting], onConnect, onReconnect, onOpen });

    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(onConnect).toHaveBeenCalledWith(fresh);
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(onReconnect).toHaveBeenCalledWith(expired);
    expect(onOpen).not.toHaveBeenCalled();
    // A member waiting on a shared credential has nothing to press.
    expect(screen.getByText('Waiting for admin')).toBeTruthy();
    const waitingRow = screen.getAllByTestId('mcp-server-row').find((row) => row.textContent?.includes('Waiting for admin'));
    expect(within(waitingRow!).queryByRole('button')).toBeNull();
    // Each row's name is its link, so no control sits inside another.
    for (const row of screen.getAllByTestId('mcp-server-row')) {
      expect(row.getAttribute('role')).toBeNull();
      expect(within(row).getAllByRole('link')).toHaveLength(1);
    }

    fireEvent.click(screen.getByRole('link', { name: 'Waiting' }));
    expect(onOpen).toHaveBeenCalledWith(waiting);
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
    expect(screen.queryByText('One shared credential for everyone')).toBeNull();

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
    expect(screen.queryByPlaceholderText('15')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
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
    expect(screen.getByRole('radio', { name: /Each person uses their own/, checked: true })).toBeTruthy();
    expect(screen.getByRole('radio', { name: /One shared credential for everyone/, checked: false })).toBeTruthy();
  });

  it('tells the page a new server was created, so it can open it', async () => {
    const onSaved = vi.fn();
    renderInTheme(
      <McpInstanceConfigPanel
        scope="personal"
        customStdioAllowed={false}
        state={{ open: true, mode: 'create', editingInstance: null, prefillTemplate: null }}
        templates={[]}
        instances={[]}
        onOpenChange={noop}
        onSaved={onSaved}
      />,
    );

    const [nameInput] = screen.getAllByRole('textbox');
    fireEvent.change(nameInput, { target: { value: 'Mine' } });
    fireEvent.change(screen.getByPlaceholderText('https://example.com/mcp'), { target: { value: 'https://mcp.example.com/mcp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ id: 'new-1', created: true }));
  });

  it('asks whose credentials people use in plain words, and saves the shared choice', async () => {
    renderPanel('org', false);
    choose('No authentication', 'API token');

    expect(screen.queryByText('Use shared admin authentication for all users')).toBeNull();
    expect(screen.getByRole('radiogroup', { name: 'How people sign in' })).toBeTruthy();
    fireEvent.click(screen.getByText('One shared credential for everyone'));
    const [nameInput] = screen.getAllByRole('textbox');
    fireEvent.change(nameInput, { target: { value: 'Team' } });
    fireEvent.change(screen.getByPlaceholderText('https://example.com/mcp'), { target: { value: 'https://mcp.example.com/mcp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mcpApi.createInstance).toHaveBeenCalledOnce());
    expect(mcpApi.createInstance.mock.calls[0][0]).toMatchObject({ authMode: 'api_token', useAdminAuth: true });
  });

  it('names the catalog server being added, and keeps an OAuth client the provider does not need under Advanced', async () => {
    mcpApi.discoverOAuthMetadata.mockResolvedValue({ supportsDcr: true, metadataFound: true, scopesSupported: [] });
    renderInTheme(
      <McpInstanceConfigPanel
        scope="org"
        customStdioAllowed={false}
        state={{ open: true, mode: 'create', editingInstance: null, prefillTemplate: template({ defaultUrl: 'https://mcp.remote.example.com/mcp' }) }}
        templates={[]}
        instances={[]}
        onOpenChange={noop}
        onSaved={noop}
      />,
    );

    expect(screen.getAllByText('Add Remote One').length).toBeGreaterThan(0);
    await waitFor(() => expect(mcpApi.discoverOAuthMetadata).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText('Client ID')).toBeNull());
    const advanced = screen.getByRole('button', { name: /Advanced/ });
    fireEvent.click(advanced);
    expect(screen.getByText('Client ID')).toBeTruthy();

    // Half an OAuth client blocks Save, so Advanced stays open to say why.
    const clientId = screen.getByPlaceholderText('Enter client ID');
    fireEvent.change(clientId, { target: { value: 'abc' } });
    fireEvent.click(advanced);
    expect(screen.getByText('Enter both the client ID and client secret, or leave both blank.')).toBeTruthy();

    fireEvent.change(clientId, { target: { value: '' } });
    fireEvent.click(advanced);
    expect(screen.queryByText('Client ID')).toBeNull();
  });

  it('keeps description and timeouts under Advanced, open when the server already uses them', () => {
    renderPanel('org');
    expect(screen.queryByText('Description')).toBeNull();
    cleanup();

    const editing = entry({ url: 'https://mcp.example.com/mcp', callTimeoutSeconds: 120 });
    renderInTheme(
      <McpInstanceConfigPanel
        scope="org"
        customStdioAllowed
        state={{ open: true, mode: 'edit', editingInstance: editing, prefillTemplate: null }}
        templates={[]}
        instances={[editing]}
        onOpenChange={noop}
        onSaved={noop}
      />,
    );
    expect(screen.getByDisplayValue('120')).toBeTruthy();
    expect(screen.getByText('Description')).toBeTruthy();
  });

  it('edits a server with its settings only: its tools are on the server page', () => {
    const editing = entry({ url: 'https://mcp.example.com/mcp' });
    renderInTheme(
      <McpInstanceConfigPanel
        scope="org"
        customStdioAllowed
        state={{ open: true, mode: 'edit', editingInstance: editing, prefillTemplate: null }}
        templates={[]}
        instances={[editing]}
        onOpenChange={noop}
        onSaved={noop}
      />,
    );

    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy();
  });
});

describe('McpServerPage', () => {
  const SEARCH = { name: 'search', namespacedName: 'mcp_server_search', inputSchema: {}, kind: 'read' as const, kindSource: 'server' as const };
  const CREATE = { name: 'create_issue', namespacedName: 'mcp_server_create_issue', inputSchema: {}, kind: 'write' as const, kindSource: 'none' as const };
  const DELETE = { name: 'delete_issue', namespacedName: 'mcp_server_delete_issue', inputSchema: {}, kind: 'destructive' as const, kindSource: 'name' as const };

  function pageElement(instance: McpMyServerEntry, props: Partial<React.ComponentProps<typeof McpServerPage>> = {}) {
    return (
      <McpServerPage
        instance={instance}
        templates={[]}
        isAdmin
        rulesEditable={false}
        isBusy={false}
        onBack={noop}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onDisconnect={noop}
        {...props}
      />
    );
  }

  function renderPage(instance: McpMyServerEntry, props: Partial<React.ComponentProps<typeof McpServerPage>> = {}) {
    const handlers = { onBack: vi.fn(), onReauthenticate: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn(), onDisconnect: vi.fn(), ...props };
    renderInTheme(pageElement(instance, handlers));
    return handlers;
  }

  const status = () => screen.getByTestId('mcp-server-status');

  function apiError(statusCode: number, message: string) {
    return { type: ErrorType.SERVER_ERROR, message, statusCode };
  }

  beforeEach(() => {
    mcpApi.getMyToolRules.mockResolvedValue({ tools: {} });
    mcpApi.updateMyToolRules.mockResolvedValue({ tools: {} });
    mcpApi.getInstanceTools.mockResolvedValue({ tools: [SEARCH, CREATE, DELETE], syncedAt: Date.now() - 120_000 });
  });

  describe('on the team page', () => {
    it('lists its tools read-only, grouped, and says organization rules are in the Enterprise Edition', async () => {
      renderPage(entry({ createdBy: '' }));

      const list = await screen.findByTestId('mcp-tool-list');
      expect(within(list).getByText('Delete issue').getAttribute('title')).toBe('delete_issue');
      expect(within(list).queryByRole('radio')).toBeNull();
      const groups = within(list).getAllByTestId(/^tool-group-/).map((group) => group.dataset.testid);
      expect(groups).toEqual(['tool-group-destructive', 'tool-group-write', 'tool-group-read']);
      const notice = screen.getByTestId('enterprise-feature-notice');
      expect(notice.textContent).toContain('Organization approval rules');
      expect(notice.textContent).toContain('Available in Enterprise');
      expect(mcpApi.getInstanceTools).toHaveBeenCalledWith('inst-1', { cached: true });
      expect(mcpApi.getMyToolRules).not.toHaveBeenCalled();
      expect(screen.queryByTestId('mcp-rules-footer')).toBeNull();
      expect(screen.getByText(/^Synced /)).toBeTruthy();
    });

    it('refreshes live', async () => {
      renderPage(entry({ createdBy: '' }));
      await screen.findByTestId('mcp-tool-list');

      fireEvent.click(screen.getByRole('button', { name: 'Refresh the tool list' }));

      await waitFor(() => expect(mcpApi.getInstanceTools).toHaveBeenLastCalledWith('inst-1', { cached: false }));
    });

    it("says when the list couldn't load, and tries again", async () => {
      mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(502, "Couldn't load this MCP server's tools."));
      renderPage(entry({ createdBy: '' }));

      const alert = await screen.findByRole('alert');
      expect(within(alert).getByText("Couldn't load the tool list")).toBeTruthy();
      expect(within(status()).getByText("Can't reach server")).toBeTruthy();

      fireEvent.click(within(alert).getByRole('button', { name: /Try again/ }));
      expect(await screen.findByTestId('mcp-tool-list')).toBeTruthy();
    });

    it('shows an expired sign-in with Reauthenticate when the server refuses the sign-in', async () => {
      mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(409, 'Reconnect needed'));
      const handlers = renderPage(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true }));

      expect(await screen.findByText('Sign-in expired')).toBeTruthy();
      expect(within(status()).getByText('Reconnect needed')).toBeTruthy();
      fireEvent.click(screen.getAllByRole('button', { name: 'Reauthenticate' })[0]);
      expect(handlers.onReauthenticate).toHaveBeenCalled();
    });

    const asksForSignIn = () => apiError(409, "This server asks for a sign-in, but it's set to No authentication.");

    it('a server set to No authentication that asks for a sign-in says so, and Edit server opens its settings', async () => {
      mcpApi.getInstanceTools.mockRejectedValueOnce(asksForSignIn());
      const handlers = renderPage(entry({ createdBy: '', isAuthenticated: true }));

      expect(await screen.findByText('This server asks for a sign-in')).toBeTruthy();
      expect(within(status()).getByText('Sign-in needed')).toBeTruthy();
      expect(screen.queryByText("Couldn't load the tool list")).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Edit server' }));
      expect(handlers.onEdit).toHaveBeenCalled();
      expect(handlers.onReauthenticate).not.toHaveBeenCalled();
    });

    it('the sign-in answer wins over an older listing that only timed out', async () => {
      mcpApi.getInstanceTools.mockRejectedValueOnce(asksForSignIn());
      renderPage(entry({ createdBy: '', isAuthenticated: true, toolsErrorCode: 'timeout', toolsTimedOut: true }));

      expect(await screen.findByText('This server asks for a sign-in')).toBeTruthy();
      expect(within(status()).getByText('Sign-in needed')).toBeTruthy();
    });

    it('lists the tools again once someone signs in again', async () => {
      mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(409, 'Reconnect needed'));
      const instance = entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true, connectedAt: 1 });
      const { rerender } = render(<Theme>{pageElement(instance)}</Theme>);
      expect(await screen.findByText('Sign-in expired')).toBeTruthy();

      rerender(<Theme>{pageElement({ ...instance, connectedAt: 2 })}</Theme>);

      expect(await screen.findByTestId('mcp-tool-list')).toBeTruthy();
      expect(screen.queryByText('Sign-in expired')).toBeNull();
    });

    it('an expired sign-in wins over an older listing that only timed out', async () => {
      mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(409, 'Reconnect needed'));
      renderPage(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true, toolsErrorCode: 'timeout', toolsTimedOut: true }));

      expect(await screen.findByText('Sign-in expired')).toBeTruthy();
      expect(within(status()).getByText('Reconnect needed')).toBeTruthy();
    });

    it('reads the list again after a new sign-in, even when the last read worked', async () => {
      mcpApi.getInstanceTools.mockResolvedValueOnce({ tools: [CREATE], syncedAt: Date.now() });
      const instance = entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true, connectedAt: 1 });
      const { rerender } = render(<Theme>{pageElement(instance)}</Theme>);
      expect(within(await screen.findByTestId('mcp-tool-list')).getByText('Create issue')).toBeTruthy();

      mcpApi.getInstanceTools.mockResolvedValueOnce({ tools: [SEARCH], syncedAt: Date.now() });
      rerender(<Theme>{pageElement({ ...instance, connectedAt: 2 })}</Theme>);

      await waitFor(() => expect(within(screen.getByTestId('mcp-tool-list')).queryByText('Create issue')).toBeNull());
      expect(mcpApi.getInstanceTools).toHaveBeenCalledTimes(2);
    });

    it("never shows one server's list on another server's page", async () => {
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
      const { rerender } = render(<Theme>{pageElement(a)}</Theme>);
      await waitFor(() => expect(mcpApi.getInstanceTools).toHaveBeenCalledWith('inst-a', { cached: true }));

      rerender(<Theme>{pageElement(b)}</Theme>);
      expect(within(await screen.findByTestId('mcp-tool-list')).getByText('Create issue')).toBeTruthy();

      answerA({ tools: [SEARCH, DELETE], syncedAt: Date.now() });
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(screen.queryByText('Search')).toBeNull();
    });

    it('asks to sign in before listing tools', async () => {
      renderPage(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: false }));

      expect(await screen.findByText("Sign in to see this server's tools.")).toBeTruthy();
      expect(screen.getByTestId('enterprise-feature-notice')).toBeTruthy();
      expect(mcpApi.getInstanceTools).not.toHaveBeenCalled();
    });

    it('names the catalog entry it came from, and says when that entry was replaced', () => {
      renderPage(entry({ createdBy: '', typeId: 'old', isCustom: false }), {
        templates: [
          template({ typeId: 'old', displayName: 'Old Slack', replacedBy: 'slack' }),
          template({ typeId: 'slack', displayName: 'Slack' }),
        ],
      });

      expect(screen.getByText(/^Old Slack · /)).toBeTruthy();
      expect(screen.getByText('No longer maintained. Add Slack instead.')).toBeTruthy();
    });
  });

  describe('on the personal page', () => {
    const personal = { rulesEditable: true, isAdmin: false };

    it('edits their own rules, grouped, starting each tool from what it does to data', async () => {
      renderPage(entry({ createdBy: '' }), personal);

      const search = within(await screen.findByRole('radiogroup', { name: 'search' }));
      expect(search.getByRole('radio', { name: 'Pre-approved', checked: true })).toBeTruthy();
      expect(within(screen.getByRole('radiogroup', { name: 'delete_issue' })).getByRole('radio', { name: 'Deny', checked: true })).toBeTruthy();
      expect(mcpApi.getMyToolRules).toHaveBeenCalledWith('inst-1');
      expect(screen.queryByTestId('enterprise-feature-notice')).toBeNull();
      expect(screen.getByText('All rules saved')).toBeTruthy();
    });

    it('counts unsaved changes, discards them, and saves', async () => {
      renderPage(entry({ createdBy: '' }), personal);

      const create = within(await screen.findByRole('radiogroup', { name: 'create_issue' }));
      fireEvent.click(create.getByRole('radio', { name: 'Pre-approved' }));
      expect(screen.getByText('1 unsaved change')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
      expect(screen.getByText('All rules saved')).toBeTruthy();

      fireEvent.click(create.getByRole('radio', { name: 'Pre-approved' }));
      fireEvent.click(within(screen.getByRole('radiogroup', { name: 'delete_issue' })).getByRole('radio', { name: 'Allow on approval' }));
      expect(screen.getByText('2 unsaved changes')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Save rules' }));

      await waitFor(() =>
        expect(mcpApi.updateMyToolRules).toHaveBeenCalledWith('inst-1', { tools: { create_issue: 'allow', delete_issue: 'ask' } })
      );
      expect(await screen.findByText('All rules saved')).toBeTruthy();
    });

    it('asks before leaving with unsaved rule changes', async () => {
      const handlers = renderPage(entry({ createdBy: '' }), personal);
      fireEvent.click(within(await screen.findByRole('radiogroup', { name: 'create_issue' })).getByRole('radio', { name: 'Deny' }));

      fireEvent.click(screen.getByRole('button', { name: /MCP servers/ }));
      expect(await screen.findByText('Discard unsaved rule changes?')).toBeTruthy();
      expect(handlers.onBack).not.toHaveBeenCalled();
      fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Discard' }));
      expect(handlers.onBack).toHaveBeenCalled();
    });

    it('goes back straight away when nothing is unsaved', async () => {
      const handlers = renderPage(entry({ createdBy: '' }), personal);
      await screen.findByRole('radiogroup', { name: 'create_issue' });

      fireEvent.click(screen.getByRole('button', { name: /MCP servers/ }));
      expect(handlers.onBack).toHaveBeenCalled();
    });

    it('keeps rules saved by name editable before signing in', async () => {
      mcpApi.getMyToolRules.mockResolvedValue({ tools: { delete_repo: 'ask' } });
      renderPage(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: false }), personal);

      expect(await screen.findByText("Sign in to see this server's tools.")).toBeTruthy();
      const row = within(await screen.findByRole('radiogroup', { name: 'delete_repo' }));
      expect(screen.getByText(/Rules set by name/)).toBeTruthy();
      fireEvent.click(row.getByRole('radio', { name: 'Deny' }));
      fireEvent.click(screen.getByRole('button', { name: 'Save rules' }));

      // A deleting name starts at Deny, so the rule needn't be stored.
      await waitFor(() => expect(mcpApi.updateMyToolRules).toHaveBeenCalledWith('inst-1', { tools: {} }));
      expect(mcpApi.getInstanceTools).not.toHaveBeenCalled();
    });

    it('leaves a refused shared credential to an admin', async () => {
      mcpApi.getInstanceTools.mockRejectedValueOnce(apiError(409, 'Reconnect needed'));
      renderPage(entry({ createdBy: '', authMode: 'api_token', useAdminAuth: true, isAuthenticated: true }), personal);

      expect(await within(status()).findByText('Waiting for admin')).toBeTruthy();
      expect(screen.queryByRole('button', { name: /^(Connect|Reauthenticate)$/ })).toBeNull();
    });

    it("says when their rules couldn't load, without a save bar", async () => {
      mcpApi.getMyToolRules.mockRejectedValueOnce(apiError(500, 'Rules are unavailable right now.'));
      renderPage(entry({ createdBy: '' }), personal);

      expect(await screen.findByText('Rules are unavailable right now.')).toBeTruthy();
      expect(screen.queryByTestId('mcp-rules-footer')).toBeNull();
      expect(within(await screen.findByTestId('mcp-tool-list')).getByText('Create issue')).toBeTruthy();
    });

    it('offers no Edit on a server someone else added', () => {
      renderPage(entry({ createdBy: '' }), { ...personal, onEdit: undefined, onDelete: undefined });
      expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    });
  });

  describe('just added', () => {
    it('says the next step is to sign in, and goes away when dismissed', () => {
      const onDismissAdded = vi.fn();
      renderPage(entry({ name: 'Jira', authMode: 'oauth', isAuthenticated: false }), { justAdded: true, onDismissAdded });

      expect(within(screen.getByTestId('mcp-server-added')).getByText('Jira is added. Sign in below so its tools can run.')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Done' }));
      expect(onDismissAdded).toHaveBeenCalled();
    });

    it('says it is ready when nothing is left to do', () => {
      renderPage(entry({ name: 'Docs', createdBy: '' }), { justAdded: true });
      expect(screen.getByText('Docs is added and ready to use.')).toBeTruthy();
    });

    it('is not shown on a server opened from the list', () => {
      renderPage(entry({ createdBy: '' }));
      expect(screen.queryByTestId('mcp-server-added')).toBeNull();
    });
  });

  describe('More actions', () => {
    function openMenu() {
      fireEvent.keyDown(screen.getByRole('button', { name: 'More actions' }), { key: 'Enter' });
    }

    it('disconnects only after asking', async () => {
      const handlers = renderPage(entry({ createdBy: '', authMode: 'oauth', isAuthenticated: true, url: 'https://mcp.example.com/mcp' }));
      openMenu();

      fireEvent.click(await screen.findByRole('menuitem', { name: /Disconnect/ }));
      expect(handlers.onDisconnect).not.toHaveBeenCalled();
      expect(await screen.findByText('Disconnect Server?')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
      expect(handlers.onDisconnect).toHaveBeenCalled();
    });

    it('removes the server through the page', async () => {
      const handlers = renderPage(entry({ createdBy: '' }));
      openMenu();

      fireEvent.click(await screen.findByRole('menuitem', { name: /Delete server/ }));
      expect(handlers.onDelete).toHaveBeenCalled();
    });

    it('copies the server URL', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
      renderPage(entry({ createdBy: '', url: 'https://mcp.example.com/mcp' }));
      openMenu();

      fireEvent.click(await screen.findByRole('menuitem', { name: /Copy server URL/ }));
      await waitFor(() => expect(writeText).toHaveBeenCalledWith('https://mcp.example.com/mcp'));
    });
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
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(warning).toHaveBeenCalledOnce());
    expect(warning.mock.calls[0][0]).toMatch(/Saved credentials were removed/);
    expect(success).not.toHaveBeenCalled();
  });
});
