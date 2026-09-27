import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({
  getRegistryConnectors: vi.fn(),
  getActiveConnectors: vi.fn(),
  getConnectorConfig: vi.fn(),
  getConnectorInstance: vi.fn(),
  toggleConnector: vi.fn(),
  resyncConnector: vi.fn(),
}));
vi.mock('../../api', () => ({ ConnectorsApi: api }));

const nav = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  params: new URLSearchParams(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
  useSearchParams: () => nav.params,
}));

vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('../../components/connector-panel', () => ({
  ConnectorPanel: () => {
    const open = useConnectorsStore((s) => s.isPanelOpen);
    const connector = useConnectorsStore((s) => s.panelConnector);
    return open ? <div role="dialog" aria-label={`Set up ${connector?.name}`} /> : null;
  },
}));
vi.mock('../../components/instance-panel', () => ({ InstanceManagementPanel: () => null }));
vi.mock('@/config', () => ({ PermissionLockIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: ({ label }: { label?: string }) => <div role="status">{label}</div>,
}));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({ useUserDirectoryEntry: () => null }));

import PersonalConnectorsPage from '../connectorsPersonalPage';
import { useConnectorsStore } from '../../store';
import {
  installDomShims,
  renderInTheme,
  signInAs,
  makeConnector,
  makeInstance,
  makeConfig,
  toasts,
  clearToasts,
} from '../../__tests__/fixtures';

const gmail = makeConnector({ name: 'Gmail', type: 'Gmail', appGroup: 'Google Workspace', scope: 'personal' });
const localFolder = makeConnector({
  name: 'Local Folder',
  type: 'LOCAL_FS',
  appGroup: 'Local',
  scope: 'personal',
});

const toastTitles = () => toasts().map((t) => t.title);

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  clearToasts();
  signInAs('member');
  nav.push.mockReset();
  nav.replace.mockReset();
  nav.params = new URLSearchParams();
  for (const fn of Object.values(api)) fn.mockReset();
  api.getRegistryConnectors.mockResolvedValue({ connectors: [gmail, localFolder] });
  api.getActiveConnectors.mockResolvedValue({ connectors: [] });
  api.getConnectorConfig.mockResolvedValue(makeConfig());
  api.toggleConnector.mockResolvedValue({});
});
afterEach(() => cleanup());

describe('Personal connectors page', () => {
  it('lets a member browse their personal connectors', async () => {
    renderInTheme(<PersonalConnectorsPage />);

    expect(await screen.findByTestId('connector-card-Gmail')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Your Personal Connectors' })).toBeTruthy();
    expect(api.getRegistryConnectors).toHaveBeenCalledWith('personal');
    expect(api.getActiveConnectors).toHaveBeenCalledWith('personal');
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('filters by active and inactive instances', async () => {
    api.getActiveConnectors.mockResolvedValue({
      connectors: [makeInstance({ type: 'Gmail', name: 'Work mail', isActive: false, scope: 'personal' })],
    });
    nav.params = new URLSearchParams('tab=active');
    const { unmount } = renderInTheme(<PersonalConnectorsPage />);
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText('No connectors found')).toBeTruthy();
    unmount();

    nav.params = new URLSearchParams('tab=inactive');
    renderInTheme(<PersonalConnectorsPage />);
    expect(await screen.findByTestId('connector-card-Gmail')).toBeTruthy();
    expect(screen.queryByTestId('connector-card-LOCAL_FS')).toBeNull();
  });

  it('starts personal setup for a connector', async () => {
    renderInTheme(<PersonalConnectorsPage />);
    const card = await screen.findByTestId('connector-card-Gmail');

    fireEvent.click(within(card).getByRole('button', { name: /Setup/ }));

    expect(await screen.findByRole('dialog', { name: 'Set up Gmail' })).toBeTruthy();
    expect(useConnectorsStore.getState().selectedScope).toBe('personal');
  });

  it('explains that a local folder can only be set up in the desktop app', async () => {
    renderInTheme(<PersonalConnectorsPage />);
    const card = await screen.findByTestId('connector-card-LOCAL_FS');

    fireEvent.click(within(card).getByRole('button', { name: /Setup/ }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'info',
      title: 'Desktop app required',
      description:
        'Local filesystem connector is only available in the PipesHub desktop app. Please use the desktop app to set up this connector.',
    });
    expect(screen.queryByRole('dialog', { name: 'Set up Local Folder' })).toBeNull();
  });

  it('reports a failure when neither list loads', async () => {
    api.getRegistryConnectors.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down' });
    api.getActiveConnectors.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down' });
    renderInTheme(<PersonalConnectorsPage />);

    await waitFor(() => expect(toastTitles()).toContain('Failed to load connectors'));
  });

  it('turns sync on for one of the member’s instances', async () => {
    const instance = makeInstance({ type: 'Gmail', name: 'Work mail', isActive: false, scope: 'personal' });
    nav.params = new URLSearchParams('connectorType=Gmail');
    api.getActiveConnectors.mockResolvedValue({ connectors: [instance] });
    api.getConnectorInstance.mockResolvedValue({ ...instance, isActive: true });
    renderInTheme(<PersonalConnectorsPage />);
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    fireEvent.click(toggle);

    await waitFor(() => expect(api.toggleConnector).toHaveBeenCalledWith('conn-1', 'sync', undefined));
    await waitFor(() => expect(toastTitles()).toContain('Connector sync enabled'));
  });
});
