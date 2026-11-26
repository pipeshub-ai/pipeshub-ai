import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { McpManageAccessProps } from '../mcp-manage-access';

const mocks = vi.hoisted(() => ({
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  authenticate: vi.fn(),
  saveAccess: vi.fn(),
  section: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/toast-store', () => ({ toast: mocks.toast }));
vi.mock('@/lib/api', () => ({ apiClient: { post: vi.fn() }, isProcessedError: () => false }));
// Stands in for a section that saves who may use the server: it registers its save.
vi.mock('@/config', () => ({
  isMcpInstanceReadOnly: () => false,
  McpInheritedCallout: () => null,
  McpManageAccess: (props: McpManageAccessProps) => {
    mocks.section(props);
    props.saveRef.current = mocks.saveAccess;
    return <div data-testid="manage-access" />;
  },
  McpInstanceConfigExtras: () => null,
}));
vi.mock('../../../api', () => ({
  McpServersApi: {
    createInstance: mocks.createInstance,
    updateInstance: mocks.updateInstance,
    authenticate: mocks.authenticate,
    discoverOAuthMetadata: vi.fn(),
    getOAuthConfig: vi.fn(),
  },
}));
vi.mock('../../../components', () => ({ McpDisabledCallout: () => null, McpDisconnectDialog: () => null, isMcpInstanceDisabled: () => false }));
vi.mock('../../../../components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({
    open,
    children,
    onPrimaryClick,
    onOpenChange,
  }: {
    open: boolean;
    children: React.ReactNode;
    onPrimaryClick: () => void;
    onOpenChange: (open: boolean) => void;
  }) =>
    open ? (
      <div>
        {children}
        <button type="button" onClick={onPrimaryClick}>
          save
        </button>
        <button type="button" onClick={() => onOpenChange(false)}>
          close
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
  SelectDropdown: ({
    value,
    onChange,
    options,
  }: {
    value: string;
    onChange: (value: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
  FormFieldTextField: ({ size: _size, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { size?: string }) => (
    <input {...props} />
  ),
  TagInput: () => null,
  ConfirmationDialog: () => null,
}));

import { McpInstanceConfigPanel } from '../mcp-instance-config-panel';

function panel(scope: 'org' | 'personal', onSaved = vi.fn(), onOpenChange = vi.fn(), open = true) {
  return (
    <Theme>
      <McpInstanceConfigPanel
        state={{ open, mode: 'create', editingInstance: null, prefillTemplate: null }}
        templates={[]}
        instances={[]}
        customStdioAllowed={false}
        onOpenChange={onOpenChange}
        onSaved={onSaved}
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
  });

  const fillAndSave = () => {
    typeInto('workspace.mcpServers.form.name', 'Internal tools');
    typeInto('workspace.mcpServers.form.url', 'https://mcp.example.com');
    fireEvent.click(screen.getByText('save'));
  };

  it('saves access only after the credentials are saved, and reports success and closes only after access is saved', async () => {
    mocks.createInstance.mockResolvedValue({ _id: 'new-1' });
    let finishCredentials: () => void = () => undefined;
    mocks.authenticate.mockReturnValue(new Promise<void>((resolve) => (finishCredentials = resolve)));
    let finishAccess: () => void = () => undefined;
    mocks.saveAccess.mockReturnValue(new Promise<void>((resolve) => (finishAccess = resolve)));
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    render(panel('org', onSaved, onOpenChange));
    fireEvent.change(within(screen.getByRole('group', { name: 'workspace.mcpServers.form.authMode' })).getByRole('combobox'), {
      target: { value: 'api_token' },
    });
    const token = screen.getByRole('group', { name: 'workspace.mcpServers.form.apiToken' }).querySelector('input');
    fireEvent.change(token as HTMLInputElement, { target: { value: 'secret-token' } });
    fillAndSave();

    await waitFor(() => expect(mocks.authenticate).toHaveBeenCalledWith('new-1', { apiToken: 'secret-token' }));
    expect(mocks.saveAccess).not.toHaveBeenCalled();

    finishCredentials();
    await waitFor(() => expect(mocks.saveAccess).toHaveBeenCalledWith('new-1'));
    expect(onSaved).not.toHaveBeenCalled();
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();

    finishAccess();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(mocks.toast.success).toHaveBeenCalledWith('workspace.mcpServers.toasts.created');
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it('keeps the panel open with an access error when saving access fails', async () => {
    mocks.createInstance.mockResolvedValue({ _id: 'new-1' });
    mocks.saveAccess.mockRejectedValue(new Error('access store down'));
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    render(panel('org', onSaved, onOpenChange));
    fillAndSave();

    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalled());
    expect(mocks.toast.error.mock.calls[0]?.[0]).toBe('workspace.mcpServers.toasts.accessSaveError');
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByTestId('manage-access')).toBeTruthy();
  });

  it('a retry after a failed access save updates the server it created instead of creating another', async () => {
    mocks.createInstance.mockResolvedValue({ _id: 'new-1' });
    mocks.updateInstance.mockResolvedValue({ _id: 'new-1' });
    mocks.saveAccess.mockRejectedValueOnce(new Error('access store down')).mockResolvedValueOnce(undefined);
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    render(panel('org', onSaved, onOpenChange));
    fillAndSave();
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalled());

    fireEvent.click(screen.getByText('save'));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(mocks.createInstance).toHaveBeenCalledTimes(1);
    expect(mocks.updateInstance).toHaveBeenCalledTimes(1);
    expect(mocks.updateInstance.mock.calls[0]?.[0]).toBe('new-1');
    expect(mocks.saveAccess.mock.calls).toEqual([['new-1'], ['new-1']]);
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('cannot be closed while a save is in flight', async () => {
    let finishCreate: (value: { _id: string }) => void = () => undefined;
    mocks.createInstance.mockReturnValue(new Promise((resolve) => (finishCreate = resolve)));
    mocks.saveAccess.mockResolvedValue(undefined);
    const onOpenChange = vi.fn();
    render(panel('org', vi.fn(), onOpenChange));
    fillAndSave();
    await waitFor(() => expect(mocks.createInstance).toHaveBeenCalled());

    fireEvent.click(screen.getByText('close'));
    expect(onOpenChange).not.toHaveBeenCalled();

    finishCreate({ _id: 'new-1' });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('a save that resolves after the panel was closed and reopened leaves the new session alone', async () => {
    let finishOld: (value: { _id: string }) => void = () => undefined;
    mocks.createInstance
      .mockReturnValueOnce(new Promise((resolve) => (finishOld = resolve)))
      .mockResolvedValueOnce({ _id: 'new-2' });
    mocks.saveAccess.mockResolvedValue(undefined);
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    const { rerender } = render(panel('org', onSaved, onOpenChange));
    fillAndSave();
    await waitFor(() => expect(mocks.createInstance).toHaveBeenCalledTimes(1));

    // Closed and reopened by the page, as when another server is opened for editing.
    rerender(panel('org', onSaved, onOpenChange, false));
    rerender(panel('org', onSaved, onOpenChange, true));
    finishOld({ _id: 'old-1' });
    await new Promise((r) => setTimeout(r, 0));
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.saveAccess).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();

    fillAndSave();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(mocks.createInstance).toHaveBeenCalledTimes(2);
    expect(mocks.updateInstance).not.toHaveBeenCalled();
    expect(mocks.saveAccess.mock.calls).toEqual([['new-2']]);
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('has no Manage Access on a personal server', () => {
    render(panel('personal'));
    expect(screen.queryByTestId('manage-access')).toBeNull();
  });
});
