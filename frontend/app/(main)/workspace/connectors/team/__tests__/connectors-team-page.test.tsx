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

// Health gating and the drawers are covered on their own; this page test is about the list.
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
vi.mock('../../demo-data/components', () => ({ DemoDataRemovalNotice: () => null }));
vi.mock('@/config', () => ({ PermissionLockIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: ({ label }: { label?: string }) => <div role="status">{label}</div>,
}));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({ useUserDirectoryEntry: () => null }));
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import TeamConnectorsPage from '../connectorsTeamPage';
import { useConnectorsStore } from '../../store';
import {
  installDomShims,
  renderInTheme,
  signInAs,
  signOut,
  makeConnector,
  makeInstance,
  makeConfig,
  toasts,
  clearToasts,
} from '../../__tests__/fixtures';

const jira = makeConnector({ isAdminAccessRequired: true, personalConnectorType: 'Jira Personal' });
const slack = makeConnector({ name: 'Slack', type: 'Slack', appGroup: 'Slack' });

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  clearToasts();
  signInAs('admin');
  nav.push.mockReset();
  nav.replace.mockReset();
  nav.params = new URLSearchParams();
  for (const fn of Object.values(api)) fn.mockReset();
  api.getRegistryConnectors.mockResolvedValue({ connectors: [jira, slack] });
  api.getActiveConnectors.mockResolvedValue({ connectors: [] });
  api.getConnectorConfig.mockResolvedValue(makeConfig());
  api.toggleConnector.mockResolvedValue({});
});
afterEach(() => cleanup());

describe('Team connectors page: access', () => {
  it('sends a member to their personal connectors instead', async () => {
    signInAs('member');
    renderInTheme(<TeamConnectorsPage />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/workspace/connectors/personal/'));
    expect(screen.queryByTestId('connector-card-Jira')).toBeNull();
    expect(api.getRegistryConnectors).not.toHaveBeenCalled();
  });

  it('waits for the profile before deciding', () => {
    signOut();
    renderInTheme(<TeamConnectorsPage />);
    expect(nav.replace).not.toHaveBeenCalled();
    expect(api.getRegistryConnectors).not.toHaveBeenCalled();
  });
});

describe('Team connectors page: list', () => {
  it('loads team connectors and lists them', async () => {
    renderInTheme(<TeamConnectorsPage />);

    expect(await screen.findByTestId('connector-card-Jira')).toBeTruthy();
    expect(screen.getByTestId('connector-card-Slack')).toBeTruthy();
    expect(api.getRegistryConnectors).toHaveBeenCalledWith('team');
    expect(api.getActiveConnectors).toHaveBeenCalledWith('team');
  });

  it('still lists the catalog when only the configured list fails', async () => {
    api.getActiveConnectors.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down' });
    renderInTheme(<TeamConnectorsPage />);

    expect(await screen.findByTestId('connector-card-Jira')).toBeTruthy();
    expect(toasts()).toEqual([]);
  });

  it('reports a failure when neither list loads', async () => {
    api.getRegistryConnectors.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down' });
    api.getActiveConnectors.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down' });
    renderInTheme(<TeamConnectorsPage />);

    await waitFor(() => expect(toasts().map((t) => t.title)).toContain('Failed to load connectors'));
    expect(await screen.findByText('No connectors found')).toBeTruthy();
  });

  it('keeps the chosen tab in the address', async () => {
    renderInTheme(<TeamConnectorsPage />);
    await screen.findByTestId('connector-card-Jira');

    fireEvent.click(screen.getByRole('radio', { name: /^Configured/ }));
    expect(nav.replace).toHaveBeenCalledWith('/workspace/connectors/team/?tab=configured');
  });
});

describe('Team connectors page: setup that needs admin access at the provider', () => {
  it('asks first, and starts setup when the user confirms', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-Jira');

    fireEvent.click(within(card).getByRole('button', { name: /Setup/ }));

    const dialog = await screen.findByRole('dialog', { name: 'Admin access required' });
    expect(
      within(dialog).getByText(/Do you have admin access in Atlassian\?/)
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, continue setup' }));

    expect(await screen.findByRole('dialog', { name: 'Set up Jira' })).toBeTruthy();
    expect(useConnectorsStore.getState().panelConnectorId).toBeNull();
  });

  it('points a non-admin at the personal connector', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-Jira');
    fireEvent.click(within(card).getByRole('button', { name: /Setup/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Admin access required' });

    fireEvent.click(within(dialog).getByRole('button', { name: "No, I'm not an admin" }));

    const redirect = await screen.findByRole('dialog', { name: 'Use the personal connector instead' });
    fireEvent.click(within(redirect).getByRole('button', { name: 'Go to Atlassian Personal' }));
    expect(nav.push).toHaveBeenCalledWith(
      '/workspace/connectors/personal/?connectorType=Jira%20Personal'
    );
    expect(useConnectorsStore.getState().isPanelOpen).toBe(false);
  });

  it('opens setup straight away for a connector without that requirement', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-Slack');

    fireEvent.click(within(card).getByRole('button', { name: /Setup/ }));

    expect(await screen.findByRole('dialog', { name: 'Set up Slack' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Admin access required' })).toBeNull();
  });
});

describe('Team connectors page: turning sync on and off', () => {
  function openTypePage(instance = makeInstance({ isActive: false })) {
    nav.params = new URLSearchParams('connectorType=Jira');
    api.getActiveConnectors.mockResolvedValue({ connectors: [instance] });
    api.getConnectorInstance.mockResolvedValue({ ...instance, isActive: !instance.isActive });
    renderInTheme(<TeamConnectorsPage />);
    return instance;
  }

  it('enables sync and confirms it', async () => {
    openTypePage();
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    fireEvent.click(toggle);

    await waitFor(() => expect(api.toggleConnector).toHaveBeenCalledWith('conn-1', 'sync', undefined));
    await waitFor(() => expect(toasts().map((t) => t.title)).toContain('Connector sync enabled'));
  });

  it('disables sync and confirms it', async () => {
    openTypePage(makeInstance({ isActive: true }));
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    fireEvent.click(toggle);

    await waitFor(() => expect(api.toggleConnector).toHaveBeenCalledWith('conn-1', 'sync'));
    await waitFor(() => expect(toasts().map((t) => t.title)).toContain('Connector sync disabled'));
  });

  it('says it could not update the connector when the toggle fails outside the API', async () => {
    openTypePage(makeInstance({ isActive: true }));
    api.toggleConnector.mockRejectedValue(new Error('socket closed'));
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    fireEvent.click(toggle);

    await waitFor(() => expect(toasts().map((t) => t.title)).toContain('Could not update connector'));
    expect(toasts().map((t) => t.title)).not.toContain('Connector sync disabled');
  });

  it('leaves the message to the API layer when the server refused the toggle', async () => {
    openTypePage(makeInstance({ isActive: true }));
    api.toggleConnector.mockRejectedValue({ type: 'AUTHORIZATION_ERROR', message: 'Forbidden', statusCode: 403 });
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    fireEvent.click(toggle);

    await waitFor(() => expect(api.toggleConnector).toHaveBeenCalled());
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
    expect(toasts()).toEqual([]);
  });
});
