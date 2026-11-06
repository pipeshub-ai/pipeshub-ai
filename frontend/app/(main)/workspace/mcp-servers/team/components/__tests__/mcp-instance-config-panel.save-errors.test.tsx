import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen, waitFor, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const api = vi.hoisted(() => ({
  discoverOAuthMetadata: vi.fn(),
  getOAuthConfig: vi.fn(),
  revealOAuthConfig: vi.fn(),
  updateInstance: vi.fn(),
  updateOAuthConfig: vi.fn(),
}));
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toasts }));
vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() }, isProcessedError: (e: { type?: string }) => Boolean(e?.type),
  fieldErrorsOf: (e: { fieldErrors?: Record<string, string> }) => e?.fieldErrors ?? {},
}));
vi.mock('@/lib/hooks/use-secret-reveal-available', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-secret-reveal-available')>()),
  useSecretRevealAvailable: () => true,
}));
vi.mock('@/config', () => ({ isMcpInstanceReadOnly: () => false, McpInheritedCallout: () => null, McpManageAccess: () => null }));
vi.mock('../../../api', () => ({ McpServersApi: api }));
vi.mock('../../../components', () => ({ McpDisabledCallout: () => null, McpDisconnectDialog: () => null, isMcpInstanceDisabled: () => false }));
vi.mock('../../../../components/workspace-right-panel', () => ({
  useWorkspaceDrawerNestedModalHost: () => null,
  WorkspaceRightPanel: ({
    open,
    children,
    onPrimaryClick,
  }: {
    open: boolean;
    children: React.ReactNode;
    onPrimaryClick?: () => void;
  }) =>
    open ? (
      <div>
        {children}
        <button type="button" onClick={onPrimaryClick}>
          save
        </button>
      </div>
    ) : null,
}));
vi.mock('../../../../components', async () => ({
  // The real hook and input: they only read the FormField context this mock leaves empty.
  useFormValidation: (await import('../../../../components/use-form-validation')).useFormValidation,
  LabeledTextField: (await import('../../../../components/form-field')).LabeledTextField,
  FormField: ({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) => (
    <div role="group" aria-label={label}>
      {children}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  ),
  SelectDropdown: () => null,
  TagInput: () => null,
  ConfirmationDialog: () => null,
}));

import { McpInstanceConfigPanel } from '../mcp-instance-config-panel';

type PanelProps = React.ComponentProps<typeof McpInstanceConfigPanel>;
type Instance = NonNullable<PanelProps['state']['editingInstance']>;

const STORED = { configured: true, clientId: 'stored-client', clientSecret: 'stored-secret' };

function oauthServer(id: string): Instance {
  return {
    _id: id,
    name: `Server ${id}`,
    description: '',
    typeId: null,
    transport: 'streamable_http',
    authMode: 'oauth',
    useAdminAuth: false,
    url: `https://${id}.example.com/mcp`,
    hasOAuthClientConfig: true,
  } as unknown as Instance;
}

function panel(editingInstance: Instance) {
  const props = {
    state: { open: true, mode: 'edit', editingInstance, prefillTemplate: null },
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
  return (
    <Theme>
      <McpInstanceConfigPanel {...props} />
    </Theme>
  );
}

const input = (label: string) =>
  screen.getByRole('group', { name: label }).querySelector('input') as HTMLInputElement;
const clientId = () => input('workspace.mcpServers.oauthConfig.clientId');
const clientSecret = () => input('workspace.mcpServers.oauthConfig.clientSecret');


beforeEach(() => {
  api.discoverOAuthMetadata.mockResolvedValue({});
  api.getOAuthConfig.mockResolvedValue({ configured: false });
  toasts.success.mockClear();
  toasts.error.mockClear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function saveAndWait() {
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  await waitFor(() => expect(api.updateInstance).toHaveBeenCalled());
}

describe('McpInstanceConfigPanel save errors', () => {
  it('marks the URL field and shows no toast when the server names the field', async () => {
    api.updateInstance.mockRejectedValue({
      type: 'VALIDATION_ERROR',
      message: 'The MCP server URL is not allowed.',
      fieldErrors: { url: 'The MCP server URL is not allowed.' },
    });
    render(panel(oauthServer('a')));

    await saveAndWait();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('The MCP server URL is not allowed.');
    expect(screen.getByRole('group', { name: 'workspace.mcpServers.form.url' }).contains(alert)).toBe(true);
    expect(toasts.error).not.toHaveBeenCalled();
  });

  it('shows exactly one toast for a failure that names no field, and asks for no global toast', async () => {
    api.updateInstance.mockRejectedValue({ type: 'SERVER_ERROR', message: 'Could not reach the server.' });
    render(panel(oauthServer('a')));

    await saveAndWait();

    await waitFor(() => expect(toasts.error).toHaveBeenCalledTimes(1));
    expect(toasts.error).toHaveBeenCalledWith('workspace.mcpServers.toasts.saveError', {
      description: 'Could not reach the server.',
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
