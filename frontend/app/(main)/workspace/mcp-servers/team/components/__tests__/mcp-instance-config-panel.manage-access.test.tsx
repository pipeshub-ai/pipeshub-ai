import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { McpManageAccessProps } from '../mcp-manage-access';

const mocks = vi.hoisted(() => ({
  createInstance: vi.fn(),
  saveAccess: vi.fn(),
  section: vi.fn(),
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock('@/lib/api', () => ({ apiClient: { post: vi.fn() }, isProcessedError: () => false }));
// Stands in for the edition's section: it registers a save, as an edition that saves access does.
vi.mock('@/config', () => ({
  isMcpInstanceReadOnly: () => false,
  McpInheritedCallout: () => null,
  McpManageAccess: (props: McpManageAccessProps) => {
    mocks.section(props);
    props.saveRef.current = mocks.saveAccess;
    return <div data-testid="manage-access" />;
  },
}));
vi.mock('../../../api', () => ({
  McpServersApi: { createInstance: mocks.createInstance, discoverOAuthMetadata: vi.fn(), getOAuthConfig: vi.fn() },
}));
vi.mock('../../../components', () => ({ McpDisabledCallout: () => null, McpDisconnectDialog: () => null, isMcpInstanceDisabled: () => false }));
vi.mock('../../../../components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({ open, children, onPrimaryClick }: { open: boolean; children: React.ReactNode; onPrimaryClick: () => void }) =>
    open ? (
      <div>
        {children}
        <button type="button" onClick={onPrimaryClick}>
          save
        </button>
      </div>
    ) : null,
  useWorkspaceDrawerNestedModalHost: () => null,
}));
vi.mock('../../../../components', () => ({
  FormField: ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div role="group" aria-label={label}>
      {children}
    </div>
  ),
  SelectDropdown: () => null,
  TagInput: () => null,
  ConfirmationDialog: () => null,
}));

import { McpInstanceConfigPanel } from '../mcp-instance-config-panel';

function panel(scope: 'org' | 'personal', onSaved = vi.fn()) {
  return (
    <Theme>
      <McpInstanceConfigPanel
        state={{ open: true, mode: 'create', editingInstance: null, prefillTemplate: null }}
        templates={[]}
        instances={[]}
        customStdioAllowed={false}
        onOpenChange={vi.fn()}
        onSaved={onSaved}
        onRequestDelete={vi.fn()}
        busyInstanceId={null}
        onAuthenticate={vi.fn()}
        onReauthenticate={vi.fn()}
        onDisconnect={vi.fn()}
        scope={scope}
      />
    </Theme>
  );
}

const typeInto = (label: string, value: string) =>
  fireEvent.change(within(screen.getByRole('group', { name: label })).getByRole('textbox'), { target: { value } });

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('McpInstanceConfigPanel Manage Access', () => {
  it('shows the section on a new organization server and saves access with the new id once the server is saved', async () => {
    mocks.createInstance.mockResolvedValue({ _id: 'new-1' });
    const onSaved = vi.fn();
    render(panel('org', onSaved));
    expect(screen.getByTestId('manage-access')).toBeTruthy();
    expect(mocks.section).toHaveBeenLastCalledWith(expect.objectContaining({ serverId: null, disabled: false }));

    typeInto('workspace.mcpServers.form.name', 'Internal tools');
    typeInto('workspace.mcpServers.form.url', 'https://mcp.example.com');
    fireEvent.click(screen.getByText('save'));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(mocks.saveAccess).toHaveBeenCalledWith('new-1');
    expect(mocks.createInstance.mock.invocationCallOrder[0]).toBeLessThan(mocks.saveAccess.mock.invocationCallOrder[0] as number);
  });

  it('has no Manage Access on a personal server', () => {
    render(panel('personal'));
    expect(screen.queryByTestId('manage-access')).toBeNull();
  });
});
