import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { toast } from '@/lib/store/toast-store';
import type { McpMyServerEntry, McpServerInstancePayload, McpServerTemplate } from '../types';

const mcpApi = vi.hoisted(() => ({
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  discoverOAuthMetadata: vi.fn(),
  getOAuthConfig: vi.fn(),
  authenticate: vi.fn(),
  updateOAuthConfig: vi.fn(),
  getInstanceTools: vi.fn(),
}));
vi.mock('../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { mcpEditCredentialImpact } from '../edit-impact';
import { isSecretFieldName } from '../stdio-env-auth';
import { McpInstanceConfigPanel } from '../team/components/mcp-instance-config-panel';
import { McpAuthDialog } from '../components/mcp-auth-dialog';

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'Server',
    transport: 'streamable_http',
    authMode: 'api_token',
    useAdminAuth: false,
    url: 'https://mcp.example.com/mcp',
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: [],
    isCustom: true,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [],
    ...overrides,
  };
}

function payload(overrides: Partial<McpServerInstancePayload> = {}): McpServerInstancePayload {
  return {
    name: 'Server',
    typeId: null,
    transport: 'streamable_http',
    authMode: 'api_token',
    useAdminAuth: false,
    url: 'https://mcp.example.com/mcp',
    ...overrides,
  };
}

function template(overrides: Partial<McpServerTemplate> = {}): McpServerTemplate {
  return {
    typeId: 'github',
    displayName: 'GitHub',
    description: 'Repos and issues',
    transport: 'streamable_http',
    defaultAuthMode: 'api_token',
    supportedAuthModes: ['api_token', 'oauth'],
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    defaultScopes: [],
    supportsDcr: false,
    tags: [],
    ...overrides,
  };
}

const noop = () => {};

beforeEach(() => {
  installBrowserShims();
  mcpApi.createInstance.mockResolvedValue({ _id: 'new-1' });
  mcpApi.updateInstance.mockImplementation(async (id: string, body: McpServerInstancePayload) => ({
    _id: id,
    ...body,
    credentialsReset: false,
  }));
  mcpApi.authenticate.mockResolvedValue({ success: true, isAuthenticated: true });
  mcpApi.discoverOAuthMetadata.mockResolvedValue({ supportsDcr: false, metadataFound: false, scopesSupported: [] });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('mcpEditCredentialImpact', () => {
  it('a new address, program or sign-in method removes everyone’s credentials', () => {
    expect(mcpEditCredentialImpact(entry(), payload({ url: 'https://other.example.com/mcp' }))).toBe('all_credentials');
    expect(mcpEditCredentialImpact(entry(), payload({ authMode: 'headers' }))).toBe('all_credentials');
    const stdio = entry({ transport: 'stdio', url: null, command: 'npx', args: ['-y', 'pkg'] });
    expect(
      mcpEditCredentialImpact(stdio, payload({ transport: 'stdio', url: undefined, command: 'npx', args: ['-y', 'other'] })),
    ).toBe('all_credentials');
    expect(
      mcpEditCredentialImpact(entry({ authMode: 'oauth', tokenUrl: 'https://a/token' }), payload({ authMode: 'oauth', tokenUrl: 'https://b/token' })),
    ).toBe('all_credentials');
  });

  it('a name, description or timeout change keeps them', () => {
    expect(
      mcpEditCredentialImpact(entry(), payload({ name: 'Renamed', description: 'New', callTimeoutSeconds: 120 })),
    ).toBe('none');
  });

  it('treats a missing field and an empty one alike, as the backend does', () => {
    const stdio = entry({ transport: 'stdio', url: '', command: 'npx', args: undefined });
    expect(
      mcpEditCredentialImpact(stdio, payload({ transport: 'stdio', url: undefined, command: 'npx', args: [] })),
    ).toBe('none');
  });

  it('a catalog server only resets on a new sign-in method, whatever its stored copy says', () => {
    const catalog = entry({ isCustom: false, typeId: 'github', url: 'https://old.example.com/mcp' });
    expect(mcpEditCredentialImpact(catalog, payload({ typeId: 'github', url: undefined }))).toBe('none');
    expect(mcpEditCredentialImpact(catalog, payload({ typeId: 'github', url: undefined, authMode: 'oauth' }))).toBe(
      'all_credentials',
    );
  });

  it('turning the shared credential off removes only it', () => {
    const shared = entry({ useAdminAuth: true });
    expect(mcpEditCredentialImpact(shared, payload({ useAdminAuth: false }))).toBe('shared_credential');
    expect(mcpEditCredentialImpact(entry(), payload({ useAdminAuth: true }))).toBe('none');
    expect(mcpEditCredentialImpact(shared, payload({ useAdminAuth: true }))).toBe('none');
  });
});

describe('isSecretFieldName', () => {
  it.each(['CLIENT_SECRET', 'DB_PASSWORD', 'GITHUB_PAT', 'API_KEY', 'SLACK_BOT_TOKEN', 'SESSION_COOKIE', 'PRIVATE_PEM'])(
    'masks %s',
    (name) => {
      expect(isSecretFieldName(name)).toBe(true);
    },
  );

  it.each(['SLACK_TEAM_ID', 'BASE_URL', 'WORKSPACE', 'PATH', 'REGION'])('shows %s', (name) => {
    expect(isSecretFieldName(name)).toBe(false);
  });
});

function renderPanel(props: {
  mode: 'create' | 'edit';
  editing?: McpMyServerEntry | null;
  prefillTemplate?: McpServerTemplate | null;
  templates?: McpServerTemplate[];
  scope?: 'org' | 'personal';
}) {
  const editing = props.editing ?? null;
  renderInTheme(
    <McpInstanceConfigPanel
      scope={props.scope ?? 'org'}
      customStdioAllowed
      state={{ open: true, mode: props.mode, editingInstance: editing, prefillTemplate: props.prefillTemplate ?? null }}
      templates={props.templates ?? []}
      instances={editing ? [editing] : []}
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

describe('McpInstanceConfigPanel asks before an edit removes sign-ins', () => {
  it('a new address waits for confirmation, then saves', async () => {
    renderPanel({ mode: 'edit', editing: entry() });

    fireEvent.change(screen.getByDisplayValue('https://mcp.example.com/mcp'), {
      target: { value: 'https://new.example.com/mcp' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Remove saved sign-ins?')).toBeTruthy();
    expect(screen.getByText(/credentials people and agents saved for it/)).toBeTruthy();
    expect(mcpApi.updateInstance).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Save and remove sign-ins' }));

    await waitFor(() => expect(mcpApi.updateInstance).toHaveBeenCalledOnce());
    expect(mcpApi.updateInstance.mock.calls[0][1]).toMatchObject({ url: 'https://new.example.com/mcp' });
  });

  it('cancelling keeps everything as it was', async () => {
    renderPanel({ mode: 'edit', editing: entry() });

    fireEvent.change(screen.getByDisplayValue('https://mcp.example.com/mcp'), {
      target: { value: 'https://new.example.com/mcp' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Remove saved sign-ins?')).toBeNull());
    expect(mcpApi.updateInstance).not.toHaveBeenCalled();
  });

  it('a rename saves straight away', async () => {
    renderPanel({ mode: 'edit', editing: entry() });

    fireEvent.change(screen.getByDisplayValue('Server'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mcpApi.updateInstance).toHaveBeenCalledOnce());
    expect(screen.queryByText('Remove saved sign-ins?')).toBeNull();
  });

  it('says only the shared credential goes when it is turned off', async () => {
    renderPanel({ mode: 'edit', editing: entry({ useAdminAuth: true }) });

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/Turning off the shared credential removes it/)).toBeTruthy();
    expect(mcpApi.updateInstance).not.toHaveBeenCalled();
  });

  it('tells the owner of a personal server it is their own sign-in that goes', async () => {
    renderPanel({ mode: 'edit', editing: entry({ scope: 'personal' }), scope: 'personal' });

    fireEvent.change(screen.getByDisplayValue('https://mcp.example.com/mcp'), {
      target: { value: 'https://new.example.com/mcp' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/credentials you and your agents saved/)).toBeTruthy();
  });
});

describe('McpInstanceConfigPanel local command', () => {
  it('saves with only a command', async () => {
    renderPanel({ mode: 'create' });

    const [nameInput] = screen.getAllByRole('textbox');
    fireEvent.change(nameInput, { target: { value: 'Local' } });
    fireEvent.change(screen.getByPlaceholderText('npx'), { target: { value: 'uvx' } });
    const create = screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement;
    expect(create.disabled).toBe(false);
    fireEvent.click(create);

    await waitFor(() => expect(mcpApi.createInstance).toHaveBeenCalledOnce());
    expect(mcpApi.createInstance.mock.calls[0][0]).toMatchObject({
      transport: 'stdio',
      command: 'uvx',
      args: [],
      requiredEnv: [],
    });
  });

  it('says where a token goes when no variable is named', () => {
    renderPanel({ mode: 'create' });

    fireEvent.click(screen.getByText('No authentication'));
    fireEvent.click(screen.getAllByText('API token').at(-1)!);

    expect(screen.getByText(/the token is passed to the command as API_TOKEN/)).toBeTruthy();
  });

  it('masks a secret variable, not an identifier', () => {
    const slackLike = template({
      typeId: 'slackish',
      transport: 'stdio',
      supportedAuthModes: ['api_token'],
      command: 'npx',
      requiredEnv: ['CLIENT_ID', 'CLIENT_SECRET'],
    });
    renderPanel({ mode: 'create', prefillTemplate: slackLike, templates: [slackLike] });

    const inputFor = (label: string) => screen.getByText(label).parentElement!.querySelector('input')!;
    expect(inputFor('CLIENT_SECRET').type).toBe('password');
    expect(inputFor('CLIENT_ID').type).toBe('text');
  });
});

describe('McpAuthDialog', () => {
  it('uses the catalog hint and link, and says the credentials were saved', async () => {
    const success = vi.spyOn(toast, 'success');
    const onAuthenticated = vi.fn();
    renderInTheme(
      <McpAuthDialog
        instance={entry({ isCustom: false, typeId: 'github', isAuthenticated: false })}
        template={template({
          documentationUrl: 'https://docs.example.com/tokens',
          authHint: { label: 'Personal access token', placeholder: 'ghp_…', helpText: 'Needs the repo scope.' },
        })}
        open
        onOpenChange={noop}
        onAuthenticated={onAuthenticated}
      />,
    );

    expect(screen.getByText('Personal access token')).toBeTruthy();
    expect(screen.getByText('Needs the repo scope.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Where to find this' }).getAttribute('href')).toBe(
      'https://docs.example.com/tokens',
    );
    fireEvent.change(screen.getByPlaceholderText('ghp_…'), { target: { value: 'ghp_secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce());
    expect(mcpApi.authenticate).toHaveBeenCalledWith('inst-1', { apiToken: 'ghp_secret' });
    expect(success).toHaveBeenCalledWith('Credentials saved');
  });

  it('cancel is a real button', () => {
    renderInTheme(
      <McpAuthDialog instance={entry({ isAuthenticated: false })} open onOpenChange={noop} onAuthenticated={noop} />,
    );

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
  });
});
