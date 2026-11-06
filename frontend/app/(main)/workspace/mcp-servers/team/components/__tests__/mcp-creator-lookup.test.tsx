import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { User } from '@/app/(main)/workspace/users/types';
import type { McpMyServerEntry } from '../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { name?: string }) => (options?.name ? `${key}: ${options.name}` : key),
  }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() }, isProcessedError: () => false }));
const readOnly = vi.hoisted(() => ({ value: false }));
vi.mock('@/config', () => ({
  isMcpInstanceReadOnly: () => readOnly.value,
  McpInheritedBadge: () => null,
  McpInheritedCallout: () => null,
}));
vi.mock('@radix-ui/themes', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@radix-ui/themes')>()),
  Avatar: ({ src }: { src?: string }) => <span data-testid="avatar" data-src={src ?? ''} />,
}));
vi.mock('../../../api', () => ({
  McpServersApi: { getOAuthConfig: vi.fn(async () => ({ configured: false })), discoverOAuthMetadata: vi.fn(async () => ({})) },
}));
vi.mock('../../../components', () => ({
  McpDisabledCallout: () => null,
  McpDisconnectDialog: () => null,
  isMcpInstanceDisabled: () => false,
}));
vi.mock('../../../../components/workspace-right-panel', () => ({
  useWorkspaceDrawerNestedModalHost: () => null,
  WorkspaceRightPanel: ({ open, headerActions }: { open: boolean; headerActions?: React.ReactNode }) =>
    open ? <div>{headerActions}</div> : null,
}));
vi.mock('../../../../components', async () => ({
  // The real hook and input: they only read the FormField context this mock leaves empty.
  useFormValidation: (await import('../../../../components/use-form-validation')).useFormValidation,
  LabeledTextField: (await import('../../../../components/form-field')).LabeledTextField,
  FormField: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectDropdown: () => null,
  TagInput: () => null,
  ConfirmationDialog: () => null,
}));
vi.mock('../mcp-panel-header', () => ({
  McpPanelTitle: () => null,
  McpServerActionsMenu: ({ entries }: { entries: { id: string; label?: string }[] }) => (
    <ul>
      {entries.map((entry) => (
        <li key={entry.id}>{entry.label}</li>
      ))}
    </ul>
  ),
}));
const directory = vi.hoisted(() => ({ user: null as User | null, asked: [] as unknown[] }));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({
  useUserDirectoryEntry: (id: unknown) => {
    directory.asked.push(id);
    return directory.user;
  },
}));

import { McpInstanceRow } from '../mcp-instance-row';
import { McpInstanceConfigPanel } from '../mcp-instance-config-panel';

const CREATOR = '507f1f77bcf86cd799439011';
const ADA: User = {
  id: 'u1', userId: CREATOR, name: 'Ada Lovelace', hasLoggedIn: true, isActive: true,
  profilePicture: 'data:image/png;base64,AAAA',
};

function server(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-1', orgId: 'org-1', createdBy: CREATOR, name: 'Server', transport: 'streamable_http',
    authMode: 'none', useAdminAuth: false, requiredEnv: [], optionalEnv: [], isCustom: false,
    createdAt: 1, updatedAt: 1, isAuthenticated: true, tools: [], ...overrides,
  } as McpMyServerEntry;
}

function row(instance = server()) {
  const noop = vi.fn();
  return render(
    <Theme>
      <McpInstanceRow
        instance={instance}
        isBusy={false}
        onManage={noop}
        onDelete={noop}
        onAuthenticate={noop}
        onReauthenticate={noop}
        onDisconnect={noop}
        onDiscoverTools={noop}
      />
    </Theme>,
  );
}

type PanelProps = React.ComponentProps<typeof McpInstanceConfigPanel>;
function panel(open: boolean) {
  const editingInstance = server() as unknown as NonNullable<PanelProps['state']['editingInstance']>;
  const props = {
    state: { open, mode: 'edit', editingInstance, prefillTemplate: null },
    templates: [],
    instances: [],
    customStdioAllowed: true,
    onOpenChange: vi.fn(),
    onSaved: vi.fn(),
    onRequestDelete: vi.fn(),
    busyInstanceId: null,
    onAuthenticate: vi.fn(),
    onReauthenticate: vi.fn(),
    onDisconnect: vi.fn(),
  } as PanelProps;
  return render(
    <Theme>
      <McpInstanceConfigPanel {...props} />
    </Theme>,
  );
}

beforeEach(() => {
  directory.asked = [];
  directory.user = ADA;
  readOnly.value = false;
});
afterEach(() => cleanup());

describe('McpInstanceRow: created by', () => {
  it('shows the creator name and picture', () => {
    row();

    expect(screen.getByText('Ada Lovelace')).toBeTruthy();
    expect(screen.getByTestId('avatar').getAttribute('data-src')).toBe(ADA.profilePicture);
  });

  it('shows the raw id when the directory has no name for the creator', () => {
    directory.user = null;
    row();

    expect(screen.getByText(CREATOR)).toBeTruthy();
  });

  it('does not look the creator up for a read-only server', () => {
    readOnly.value = true;
    row();

    expect(directory.asked.every((id) => id === null)).toBe(true);
    expect(screen.queryByText('Ada Lovelace')).toBeNull();
  });
});

describe('McpInstanceConfigPanel: added by', () => {
  it('names the creator in the menu while the panel is open', () => {
    panel(true);

    expect(screen.getByText('workspace.mcpServers.configPanel.menu.addedBy: Ada Lovelace')).toBeTruthy();
    expect(directory.asked).toContain(CREATOR);
  });

  it('does not look the creator up while the panel is closed', () => {
    panel(false);

    expect(directory.asked.every((id) => id === null)).toBe(true);
  });

  it('leaves "Added by" out when the creator is unknown or the lookup failed', () => {
    directory.user = null;
    panel(true);

    expect(screen.queryByText(/configPanel\.menu\.addedBy/)).toBeNull();
  });
});
