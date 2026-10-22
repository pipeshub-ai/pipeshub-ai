import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset, RegistryToolsetRow } from '@/app/(main)/toolsets/api';
import { CONNECTOR_SECRET_MASK } from '@/app/(main)/workspace/connectors/constants';

const api = vi.hoisted(() => ({
  getToolsetRegistrySchema: vi.fn(),
  listToolsetOAuthConfigs: vi.fn(async () => []),
  createToolsetInstance: vi.fn(async (body: Record<string, unknown>) => ({ _id: 'new-1', ...body })),
  updateToolsetInstance: vi.fn(async () => ({})),
  deleteToolsetInstance: vi.fn(async () => ({})),
  getToolsetInstance: vi.fn(),
}));

vi.mock('@/app/(main)/toolsets/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/toolsets/api')>()),
  ToolsetsApi: api,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui', () => ({ ConnectorIcon: () => null }));
vi.mock('@/lib/api', () => ({ apiClient: { post: vi.fn(() => new Promise(() => {})) }, isProcessedError: () => false }));

import { ActionSetupPanel } from '../components/action-setup-panel';
import { ToolsetInstanceRowCard } from '../components/toolset-instance-row-card';
import { typeDetailRegistryRow } from '../types';

const OAUTH_FIELDS = [
  { name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true, usage: 'BOTH' },
  { name: 'clientSecret', displayName: 'Client secret', fieldType: 'PASSWORD', required: true, usage: 'BOTH', isSecret: true },
];
const SCHEMAS: Record<string, unknown> = {
  warehouse: {
    toolset: {
      config: {
        auth: {
          schemas: {
            BASIC_AUTH: { fields: [{ name: 'host', displayName: 'Host', fieldType: 'TEXT', required: true, usage: 'CONFIGURE' }] },
            API_TOKEN: { fields: [{ name: 'baseUrl', displayName: 'Base URL', fieldType: 'URL', required: true, usage: 'CONFIGURE' }] },
          },
        },
      },
    },
  },
  mariadb: {
    toolset: {
      config: {
        auth: {
          schemas: {
            BASIC_AUTH: {
              fields: [
                { name: 'host', displayName: 'Host', fieldType: 'TEXT', required: true, usage: 'CONFIGURE' },
                { name: 'username', displayName: 'Username', fieldType: 'TEXT', required: true, usage: 'AUTHENTICATE' },
              ],
            },
          },
        },
      },
    },
  },
  github: {
    toolset: {
      config: {
        auth: {
          schemas: {
            API_TOKEN: { fields: [{ name: 'token', displayName: 'Token', fieldType: 'PASSWORD', required: true, usage: 'AUTHENTICATE' }] },
          },
        },
      },
    },
  },
  gmail: { toolset: { config: { auth: { schemas: { OAUTH: { fields: OAUTH_FIELDS } } } } } },
  jira: {
    toolset: {
      config: {
        auth: {
          schemas: {
            API_TOKEN: {
              fields: [
                { name: 'baseUrl', displayName: 'Base URL', fieldType: 'URL', required: true, usage: 'CONFIGURE' },
                { name: 'email', displayName: 'Email', fieldType: 'TEXT', required: true, usage: 'AUTHENTICATE' },
                { name: 'apiToken', displayName: 'API token', fieldType: 'PASSWORD', required: true, usage: 'AUTHENTICATE' },
              ],
            },
          },
        },
      },
    },
  },
};

function row(name: string, authTypes: string[]): RegistryToolsetRow {
  const displayName = { gmail: 'Gmail', github: 'GitHub', mariadb: 'MariaDB' }[name] ?? 'Jira';
  return { name, displayName, description: '', category: 'app', appGroup: '', iconPath: '', supportedAuthTypes: authTypes, toolCount: 1 };
}

function personalGmail(overrides: Partial<BuilderSidebarToolset> = {}): BuilderSidebarToolset {
  return {
    name: 'gmail', normalized_name: 'gmail', displayName: 'Gmail', description: '', iconPath: '', category: 'app',
    toolCount: 1, tools: [{ name: 'send_email', fullName: 'gmail.send_email', description: '' }],
    isConfigured: true, isAuthenticated: true, isFromRegistry: false, instanceId: 'inst-g', instanceName: 'Team Gmail',
    toolsetType: 'gmail', authType: 'OAUTH', scope: 'personal', ...overrides,
  };
}

function field(name: string): HTMLInputElement {
  return document.querySelector(`[data-ph-field="${name}"] input`) as HTMLInputElement;
}

async function renderPanel(props: Partial<React.ComponentProps<typeof ActionSetupPanel>> = {}) {
  const onCreated = vi.fn();
  const onDeleted = vi.fn();
  renderInTheme(
    <ActionSetupPanel
      open
      scope="personal"
      registryRow={row('gmail', ['OAUTH'])}
      onOpenChange={vi.fn()}
      onCreated={onCreated}
      onDeleted={onDeleted}
      {...props}
    />,
  );
  await waitFor(() => expect(document.querySelector('[data-ph-field]')).toBeTruthy());
  return { onCreated, onDeleted };
}

beforeEach(() => {
  installBrowserShims();
  api.getToolsetRegistrySchema.mockImplementation(async (type: string) => SCHEMAS[type]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('adding a personal action', () => {
  it("uses the person's own OAuth app, never one of the organization's", async () => {
    const { onCreated } = await renderPanel();

    expect(api.listToolsetOAuthConfigs).not.toHaveBeenCalled();
    expect(document.querySelector('[data-ph-action-oauth-app-name]')).toBeNull();
    expect(screen.getByText(/Only you can see and use this action/)).toBeTruthy();
    fireEvent.change(field('clientId'), { target: { value: 'my-client' } });
    fireEvent.change(field('clientSecret'), { target: { value: 'my-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(api.createToolsetInstance).toHaveBeenCalledWith({
      instanceName: 'Gmail', toolsetType: 'gmail', authType: 'OAUTH',
      authConfig: { clientId: 'my-client', clientSecret: 'my-secret' }, scope: 'personal',
    }));
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ _id: 'new-1' }));
  });

  it("keeps only an API-token action's setup fields: the sign-in comes after", async () => {
    await renderPanel({ registryRow: row('jira', ['API_TOKEN']) });

    expect(field('email')).toBeNull();
    fireEvent.change(field('baseUrl'), { target: { value: 'https://me.atlassian.net' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(api.createToolsetInstance).toHaveBeenCalledWith(
      expect.objectContaining({ authConfig: { baseUrl: 'https://me.atlassian.net' }, scope: 'personal' }),
    ));
  });

  it('asks for nothing but a name when everything is part of the sign-in', async () => {
    renderInTheme(
      <ActionSetupPanel open scope="personal" registryRow={row('github', ['API_TOKEN'])} onOpenChange={vi.fn()} />,
    );
    await screen.findByText(/Only you can see and use this action/);

    expect(document.querySelector('[data-ph-field]')).toBeNull();
    expect(screen.queryByText(/No credential fields/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(api.createToolsetInstance).toHaveBeenCalledWith(
      expect.objectContaining({ instanceName: 'GitHub', toolsetType: 'github', scope: 'personal' }),
    ));
  });
});

describe('a toolset that names its server by host', () => {
  it('is left to an administrator: the panel says so and creates nothing', async () => {
    renderInTheme(
      <ActionSetupPanel open scope="personal" registryRow={row('mariadb', ['BASIC_AUTH'])} onOpenChange={vi.fn()} />,
    );

    expect(await screen.findByText(/Only an administrator can add MariaDB/)).toBeTruthy();
    expect(document.querySelector('[data-ph-field]')).toBeNull();
    const create = screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.click(create);
    expect(api.createToolsetInstance).not.toHaveBeenCalled();
  });

  it("still lets them pick another of the toolset's auth types", async () => {
    renderInTheme(
      <ActionSetupPanel open scope="personal" registryRow={row('warehouse', ['BASIC_AUTH', 'API_TOKEN'])} onOpenChange={vi.fn()} />,
    );

    expect(await screen.findByText(/Only an administrator can add/)).toBeTruthy();
    expect(screen.getByRole('combobox')).toBeTruthy();
  });
});

describe('editing a personal action', () => {
  beforeEach(() => {
    api.getToolsetInstance.mockResolvedValue({ auth: { clientId: 'my-client', clientSecret: CONNECTOR_SECRET_MASK } });
  });

  it('keeps a saved secret unless it is replaced', async () => {
    await renderPanel({ editInstance: personalGmail() });
    await waitFor(() => expect(field('clientId').value).toBe('my-client'));

    expect(field('clientSecret').value).toBe('');
    expect(screen.queryByText(/Sign in to it once it's added/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.updateToolsetInstance).toHaveBeenCalledWith('inst-g', {
      instanceName: 'Team Gmail', authConfig: { clientId: 'my-client', clientSecret: CONNECTOR_SECRET_MASK },
    }));
    expect(api.createToolsetInstance).not.toHaveBeenCalled();
  });

  it('deletes it once the person confirms', async () => {
    const { onDeleted } = await renderPanel({ editInstance: personalGmail() });

    fireEvent.click(screen.getByRole('button', { name: 'Delete action' }));
    const confirm = await screen.findByRole('alertdialog');
    expect(api.deleteToolsetInstance).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete action' }));

    await waitFor(() => expect(api.deleteToolsetInstance).toHaveBeenCalledWith('inst-g'));
    expect(onDeleted).toHaveBeenCalled();
  });
});

describe('a personal action in the list', () => {
  it("is marked as the person's own, with its sign-in and its settings both at hand", () => {
    const onManage = vi.fn();
    renderInTheme(
      <ToolsetInstanceRowCard scope="personal" instance={personalGmail()} onAuthenticate={vi.fn()} onConfigure={vi.fn()} onManage={onManage} />,
    );

    expect(screen.getByText('Yours')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Manage connection' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Manage configuration' }));
    expect(onManage).toHaveBeenCalled();
  });

  it("an organization's action isn't", () => {
    renderInTheme(
      <ToolsetInstanceRowCard scope="personal" instance={personalGmail({ scope: 'org' })} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />,
    );
    expect(screen.queryByText('Yours')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Manage configuration' })).toBeNull();
  });
});

describe("a type's page, when adding your own", () => {
  it("offers the type's sign-in types even when a filter hides every instance", () => {
    const found = typeDetailRegistryRow('jira', undefined, row('jira', ['OAUTH', 'API_TOKEN']));
    expect(found.supportedAuthTypes).toEqual(['OAUTH', 'API_TOKEN']);
  });

  it("falls back to the registry's types when the instance doesn't list them", () => {
    const listed = personalGmail({ supportedAuthTypes: undefined });
    expect(typeDetailRegistryRow('gmail', listed, row('gmail', ['OAUTH'])).supportedAuthTypes).toEqual(['OAUTH']);
    expect(typeDetailRegistryRow('gmail', listed, null).supportedAuthTypes).toEqual(['OAUTH']);
  });

  it('knows nothing to offer only when neither the list nor the registry has the type', () => {
    expect(typeDetailRegistryRow('jira', undefined, null).supportedAuthTypes).toEqual(['NONE']);
  });
});
