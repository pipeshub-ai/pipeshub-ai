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
  EnterpriseFeatureNotice: () => null,
  isMcpInstanceReadOnly: () => readOnly.value,
  McpInheritedBadge: () => null,
  McpInstanceBadges: () => null,
  McpInheritedCallout: () => null,
  useMcpInstanceAdminTab: () => null,
  McpInstanceConfigExtras: () => null,
  McpManageAccess: () => null,
}));
vi.mock('@radix-ui/themes', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@radix-ui/themes')>()),
  Avatar: ({ src }: { src?: string }) => <span data-testid="avatar" data-src={src ?? ''} />,
}));
vi.mock('../../../api', () => ({
  McpServersApi: {
    getOAuthConfig: vi.fn(async () => ({ configured: false })),
    discoverOAuthMetadata: vi.fn(async () => ({})),
    getInstanceTools: vi.fn(async () => ({ tools: [] })),
  },
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
  // The real wrapper: the panel's fields read the mocked FormField's ids through it.
  FormFieldTextField: (await vi.importActual<typeof import('../../../../components/form-field')>('../../../../components/form-field')).FormFieldTextField,
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

import { McpServerPage } from '../../../components/mcp-server-page';

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

function page(instance = server()) {
  const noop = vi.fn();
  render(
    <Theme>
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
        onDelete={noop}
      />
    </Theme>,
  );
}

beforeEach(() => {
  directory.asked = [];
  directory.user = ADA;
  readOnly.value = false;
});
afterEach(() => cleanup());

describe('McpServerPage: added by', () => {
  it('names the creator in the menu', () => {
    page();

    expect(screen.getByText('workspace.mcpServers.configPanel.menu.addedBy: Ada Lovelace')).toBeTruthy();
    expect(directory.asked).toContain(CREATOR);
  });

  it('does not look the creator up for a read-only server', () => {
    readOnly.value = true;
    page();

    expect(directory.asked.every((id) => id === null)).toBe(true);
    expect(screen.queryByText(/configPanel\.menu\.addedBy/)).toBeNull();
  });

  it('leaves "Added by" out when the creator is unknown or the lookup failed', () => {
    directory.user = null;
    page();

    expect(screen.getByText('workspace.mcpServers.configPanel.menu.remove')).toBeTruthy();
    expect(screen.queryByText(/configPanel\.menu\.addedBy/)).toBeNull();
  });
});
