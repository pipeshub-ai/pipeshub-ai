import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry, McpServerTemplate } from '../types';

const mcpApi = vi.hoisted(() => ({
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  discoverOAuthMetadata: vi.fn(),
  getOAuthConfig: vi.fn(),
  getInstanceTools: vi.fn(),
  getMyToolRules: vi.fn(async () => ({ tools: {} })),
}));
vi.mock('../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { freeServerName, serverNameKey } from '../server-names';
import { McpInstanceConfigPanel } from '../team/components/mcp-instance-config-panel';

const TAKEN = 'Another server already has this name. Choose a different one.';

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'Jira',
    typeId: 'jira',
    transport: 'streamable_http',
    authMode: 'api_token',
    useAdminAuth: false,
    url: 'https://mcp.example.com/mcp',
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

const jiraTemplate: McpServerTemplate = {
  typeId: 'jira',
  displayName: 'Jira',
  description: 'Issues',
  transport: 'streamable_http',
  defaultAuthMode: 'api_token',
  supportedAuthModes: ['api_token'],
  args: [],
  requiredEnv: [],
  optionalEnv: [],
  defaultScopes: [],
  supportsDcr: false,
  tags: [],
  defaultUrl: 'https://mcp.jira.example.com/mcp',
};

function renderPanel({
  instances,
  editing = null,
  prefill = null,
}: {
  instances: McpMyServerEntry[];
  editing?: McpMyServerEntry | null;
  prefill?: McpServerTemplate | null;
}) {
  renderInTheme(
    <McpInstanceConfigPanel
      scope="org"
      customStdioAllowed
      state={{ open: true, mode: editing ? 'edit' : 'create', editingInstance: editing, prefillTemplate: prefill }}
      templates={[jiraTemplate]}
      instances={instances}
      onOpenChange={() => {}}
      onSaved={() => {}}
    />,
  );
}

const nameInput = () => screen.getAllByRole('textbox')[0] as HTMLInputElement;
const saveButton = (label: 'Create' | 'Save') => screen.getByRole('button', { name: label }) as HTMLButtonElement;

beforeEach(() => {
  installBrowserShims();
  mcpApi.discoverOAuthMetadata.mockResolvedValue({ supportsDcr: false, metadataFound: false, scopesSupported: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('server names', () => {
  it('compare without case or extra spaces, as the backend does', () => {
    expect(serverNameKey('  Jira   Cloud ')).toBe(serverNameKey('jira cloud'));
    expect(serverNameKey('Jira-Cloud')).not.toBe(serverNameKey('Jira Cloud'));
  });

  it('a free name is the name itself, else the first numbered one nobody has', () => {
    expect(freeServerName('Jira', ['GitHub'])).toBe('Jira');
    expect(freeServerName('Jira', ['jira', 'JIRA 2'])).toBe('Jira 3');
  });
});

describe('McpInstanceConfigPanel names', () => {
  it('says a name is taken, whatever the type, and keeps Create off', () => {
    renderPanel({ instances: [entry({ name: 'Handbook', typeId: null, isCustom: true })] });

    fireEvent.change(nameInput(), { target: { value: '  handbook ' } });

    expect(screen.getByText(TAKEN)).toBeTruthy();
    expect(saveButton('Create').disabled).toBe(true);

    fireEvent.change(nameInput(), { target: { value: 'Handbook search' } });
    expect(screen.queryByText(TAKEN)).toBeNull();
  });

  it('starts another catalog server at a name nobody has', () => {
    renderPanel({ instances: [entry(), entry({ _id: 'inst-2', name: 'Jira 2' })], prefill: jiraTemplate });

    expect(nameInput().value).toBe('Jira 3');
    expect(screen.queryByText(TAKEN)).toBeNull();
  });

  it('never flags a server for keeping its own name, even one it already shares', () => {
    const twin = entry({ _id: 'inst-2' });
    renderPanel({ instances: [entry(), twin], editing: twin });

    expect(screen.queryByText(TAKEN)).toBeNull();
    fireEvent.change(nameInput(), { target: { value: 'JIRA' } });
    expect(screen.queryByText(TAKEN)).toBeNull();
  });

  it('flags renaming a server onto another one', () => {
    const other = entry({ _id: 'inst-2', name: 'Jira support' });
    renderPanel({ instances: [entry(), other], editing: other });

    fireEvent.change(nameInput(), { target: { value: 'jira' } });

    expect(screen.getByText(TAKEN)).toBeTruthy();
    expect(saveButton('Save').disabled).toBe(true);
  });
});
