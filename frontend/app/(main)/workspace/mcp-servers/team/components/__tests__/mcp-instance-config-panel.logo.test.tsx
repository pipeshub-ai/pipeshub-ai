import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen, waitFor, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const api = vi.hoisted(() => ({
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  getLogos: vi.fn(),
  setLogo: vi.fn(),
  removeLogo: vi.fn(),
  discoverOAuthMetadata: vi.fn(),
  getOAuthConfig: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
const picked = vi.hoisted(() => ({ imageFileToLogo: vi.fn() }));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(async () => ({ data: [] })) },
  isProcessedError: () => false,
}));
vi.mock('@/config', () => ({ isMcpInstanceReadOnly: () => false, McpInheritedCallout: () => null }));
vi.mock('../../../api', () => ({ McpServersApi: api }));
// jsdom has no canvas; the drawing is covered by its own tests.
vi.mock('../../../logo-file', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../logo-file')>()),
  imageFileToLogo: picked.imageFileToLogo,
}));
vi.mock('../../../components', () => ({
  McpDisabledCallout: () => null,
  McpDisconnectDialog: () => null,
  isMcpInstanceDisabled: () => false,
  McpLogoImage: ({ src }: { src: string }) => <img src={src} alt="" data-testid="logo-preview" />,
}));
vi.mock('../../../../components/workspace-right-panel', () => ({
  useWorkspaceDrawerNestedModalHost: () => null,
  WorkspaceRightPanel: ({ open, children, onPrimaryClick }: { open: boolean; children: React.ReactNode; onPrimaryClick?: () => void }) =>
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
  FormField: ({ label, description, error, children }: { label: string; description?: string; error?: string; children: React.ReactNode }) => (
    <div role="group" aria-label={label}>
      {children}
      {description && <p>{description}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  ),
  SelectDropdown: () => null,
  TagInput: () => null,
  ConfirmationDialog: () => null,
}));

import { McpInstanceConfigPanel } from '../mcp-instance-config-panel';
import { McpLogoFileError } from '../../../logo-file';
import { resetMcpLogos } from '../../../logo-store';

type PanelProps = React.ComponentProps<typeof McpInstanceConfigPanel>;
type Instance = NonNullable<PanelProps['state']['editingInstance']>;

const SAVED = 'data:image/png;base64,c2F2ZWQ=';
const NEW = 'data:image/png;base64,bmV3';
const FIRST = 'data:image/png;base64,Zmlyc3Q=';

const custom = {
  _id: 'inst-1',
  name: 'Acme tools',
  description: '',
  typeId: null,
  transport: 'streamable_http',
  authMode: 'none',
  useAdminAuth: false,
  url: 'https://mcp.acme.example/mcp',
  isCustom: true,
} as unknown as Instance;

const template = {
  typeId: 'linear',
  displayName: 'Linear',
  description: 'Issues',
  transport: 'streamable_http',
  defaultAuthMode: 'oauth',
  supportedAuthModes: ['oauth'],
  args: [],
  requiredEnv: [],
  optionalEnv: [],
  defaultScopes: [],
  supportsDcr: true,
  tags: [],
} as unknown as PanelProps['templates'][number];

function panel(state: PanelProps['state'], onSaved = vi.fn()) {
  const props = {
    state,
    templates: [template],
    instances: [],
    customStdioAllowed: false,
    onOpenChange: vi.fn(),
    onSaved,
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

const createCustom = { open: true, mode: 'create', editingInstance: null, prefillTemplate: null } as PanelProps['state'];
const editCustom = { open: true, mode: 'edit', editingInstance: custom, prefillTemplate: null } as PanelProps['state'];

const logoField = () => screen.queryByRole('group', { name: 'workspace.mcpServers.form.logo' });
const preview = () => screen.queryByTestId('logo-preview')?.getAttribute('src') ?? null;
const textInput = (label: string) => screen.getByRole('group', { name: label }).querySelector('input') as HTMLInputElement;

async function pickFile() {
  const input = screen.getByTestId('mcp-logo-input') as HTMLInputElement;
  await act(async () => {
    fireEvent.change(input, { target: { files: [new File(['x'], 'logo.png', { type: 'image/png' })] } });
  });
}

async function save() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
  });
}

beforeEach(() => {
  resetMcpLogos();
  for (const fn of [...Object.values(api), ...Object.values(toast), picked.imageFileToLogo]) fn.mockReset();
  api.getLogos.mockResolvedValue({ logos: { 'inst-1': SAVED } });
  api.createInstance.mockResolvedValue({ ...custom, _id: 'new-1' });
  api.updateInstance.mockResolvedValue(custom);
  api.setLogo.mockImplementation(async (_id: string, logo: string) => ({ logo }));
  api.removeLogo.mockResolvedValue({ success: true });
  picked.imageFileToLogo.mockResolvedValue(NEW);
});
afterEach(cleanup);

describe('McpInstanceConfigPanel logo', () => {
  it('saves the logo picked for a new custom server once the server exists', async () => {
    render(panel(createCustom));
    fireEvent.change(textInput('workspace.mcpServers.form.name'), { target: { value: 'Acme tools' } });
    fireEvent.change(textInput('workspace.mcpServers.form.url'), { target: { value: 'https://mcp.acme.example/mcp' } });

    await pickFile();
    expect(preview()).toBe(NEW);
    await save();

    expect(api.setLogo).toHaveBeenCalledWith('new-1', NEW);
    expect(api.createInstance.mock.invocationCallOrder[0]).toBeLessThan(api.setLogo.mock.invocationCallOrder[0]);
  });

  it('offers no logo for a catalog server, which shows its vendor logo', () => {
    render(panel({ open: true, mode: 'create', editingInstance: null, prefillTemplate: template } as PanelProps['state']));

    expect(logoField()).toBeNull();
  });

  it("shows a custom server's saved logo and removes it on save", async () => {
    render(panel(editCustom));
    await waitFor(() => expect(preview()).toBe(SAVED));

    fireEvent.click(screen.getByRole('button', { name: 'workspace.mcpServers.form.logoRemove' }));
    expect(preview()).toBeNull();
    await save();

    expect(api.removeLogo).toHaveBeenCalledWith('inst-1');
    expect(api.setLogo).not.toHaveBeenCalled();
  });

  it('leaves the logo alone when it was not changed', async () => {
    render(panel(editCustom));
    await waitFor(() => expect(preview()).toBe(SAVED));

    await save();

    expect(api.updateInstance).toHaveBeenCalled();
    expect(api.setLogo).not.toHaveBeenCalled();
    expect(api.removeLogo).not.toHaveBeenCalled();
  });

  it('keeps the saved server when only its logo fails, and says so', async () => {
    api.setLogo.mockRejectedValue(new Error('too large'));
    const onSaved = vi.fn();
    render(panel(editCustom, onSaved));

    await pickFile();
    await save();

    expect(toast.error).toHaveBeenCalledWith('workspace.mcpServers.toasts.logoSaveError', undefined);
    expect(onSaved).toHaveBeenCalled();
  });

  it('says why a picked file was refused and keeps the earlier logo', async () => {
    picked.imageFileToLogo.mockRejectedValue(new McpLogoFileError('type'));
    render(panel(editCustom));
    await waitFor(() => expect(preview()).toBe(SAVED));

    await pickFile();

    expect(screen.getByRole('alert').textContent).toBe('workspace.mcpServers.form.logoErrors.type');
    expect(preview()).toBe(SAVED);
  });

  it('keeps the file picked last when an earlier one finishes after it', async () => {
    let finishFirst: (logo: string) => void = () => {};
    picked.imageFileToLogo
      .mockReturnValueOnce(new Promise<string>((resolve) => (finishFirst = resolve)))
      .mockResolvedValueOnce(NEW);
    render(panel(editCustom));
    await waitFor(() => expect(preview()).toBe(SAVED));

    await pickFile();
    await pickFile();
    await act(async () => finishFirst(FIRST));

    expect(preview()).toBe(NEW);
  });

  it('keeps a logo removed while a picked file was still being read', async () => {
    let finish: (logo: string) => void = () => {};
    picked.imageFileToLogo.mockReturnValueOnce(new Promise<string>((resolve) => (finish = resolve)));
    render(panel(editCustom));
    await waitFor(() => expect(preview()).toBe(SAVED));

    await pickFile();
    fireEvent.click(screen.getByRole('button', { name: 'workspace.mcpServers.form.logoRemove' }));
    await act(async () => finish(NEW));

    expect(preview()).toBeNull();
  });
});
