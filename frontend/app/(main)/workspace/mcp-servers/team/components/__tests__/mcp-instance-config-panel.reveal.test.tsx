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

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() }, isProcessedError: () => false }));
vi.mock('@/lib/hooks/use-secret-reveal-available', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-secret-reveal-available')>()),
  useSecretRevealAvailable: () => true,
}));
vi.mock('@/config', () => ({ isMcpInstanceReadOnly: () => false, McpInheritedCallout: () => null }));
vi.mock('../../../api', () => ({ McpServersApi: api }));
vi.mock('../../../components', () => ({ McpDisabledCallout: () => null }));
vi.mock('../../../../components/workspace-right-panel', () => ({
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
vi.mock('../../../../components', () => ({
  FormField: ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div role="group" aria-label={label}>
      {children}
    </div>
  ),
  SelectDropdown: () => null,
  TagInput: () => null,
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

async function revealStored() {
  fireEvent.click(await screen.findByRole('button', { name: 'form.showStoredValues' }));
  await waitFor(() => expect(clientSecret().value).toBe(STORED.clientSecret));
}

async function save() {
  fireEvent.click(screen.getByRole('button', { name: 'save' }));
  await waitFor(() => expect(api.updateInstance).toHaveBeenCalled());
}

beforeEach(() => {
  api.discoverOAuthMetadata.mockResolvedValue({});
  api.getOAuthConfig.mockResolvedValue({ configured: true });
  api.revealOAuthConfig.mockResolvedValue(STORED);
  api.updateInstance.mockImplementation(async (id: string) => ({ _id: id }));
  api.updateOAuthConfig.mockResolvedValue({ success: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('McpInstanceConfigPanel stored OAuth client', () => {
  it('does not resave the client it only revealed', async () => {
    render(panel(oauthServer('a')));
    await revealStored();
    expect(clientId().value).toBe(STORED.clientId);

    await save();

    expect(api.updateOAuthConfig).not.toHaveBeenCalled();
  });

  it('saves the client once the admin changes a revealed value', async () => {
    render(panel(oauthServer('a')));
    await revealStored();
    fireEvent.change(clientSecret(), { target: { value: 'rotated-secret' } });

    await save();

    expect(api.updateOAuthConfig).toHaveBeenCalledWith('a', {
      clientId: STORED.clientId,
      clientSecret: 'rotated-secret',
    });
  });

  it('drops a reveal that answers after the drawer moved to another server', async () => {
    let answer: (value: typeof STORED) => void = () => {};
    api.revealOAuthConfig.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    const { rerender } = render(panel(oauthServer('a')));
    fireEvent.click(await screen.findByRole('button', { name: 'form.showStoredValues' }));

    rerender(panel(oauthServer('b')));
    await act(async () => answer(STORED));

    expect(clientId().value).toBe('');
    expect(clientSecret().value).toBe('');
    expect(screen.getByRole('button', { name: 'form.showStoredValues' })).toBeTruthy();
  });
});
