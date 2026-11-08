import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
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
vi.mock('@/config', async () => ({
  PermissionLockIcon: () => null,
  TEAM_CONNECTOR_VISIBILITY_WARNINGS: {
    ...(await import('../../utils/team-visibility-warning')).TEAM_CONNECTOR_VISIBILITY_WARNINGS,
    // No shipped connector is team-only today; this one keeps that dialog covered.
    'Team Only DB': { hasPersonal: false },
  },
}));
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

import { clearSessionCache } from '@/lib/api/session-cache';
import { writeSwrKey } from '@/lib/api/swr-cache';
import { ACTIVE_TEAM_CONNECTORS_KEY } from '../../cache-keys';
import { useDemoDataActive } from '../../demo-data/use-demo-data';
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

const jira = makeConnector({ isAdminAccessRequired: true, personalConnectorType: 'Jira personal' });
const slack = makeConnector({ name: 'Slack', type: 'Slack', appGroup: 'Slack' });
const s3 = makeConnector({ name: 'S3', type: 'S3', appGroup: 'S3' });
const teamOnly = makeConnector({ name: 'Team Only DB', type: 'Team Only DB', appGroup: 'Team Only DB' });

beforeEach(() => {
  installDomShims();
  clearSessionCache();
  useConnectorsStore.getState().reset();
  clearToasts();
  signInAs('admin');
  nav.push.mockReset();
  nav.replace.mockReset();
  nav.params = new URLSearchParams();
  for (const fn of Object.values(api)) fn.mockReset();
  api.getRegistryConnectors.mockResolvedValue({ connectors: [jira, slack, s3, teamOnly] });
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
    expect(api.getActiveConnectors).toHaveBeenCalledWith('team', 1, 100, { suppressErrorToast: true });
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

    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));

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
    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Admin access required' });

    fireEvent.click(within(dialog).getByRole('button', { name: "No, I'm not an admin" }));

    const redirect = await screen.findByRole('dialog', { name: 'Use the personal connector instead' });
    fireEvent.click(within(redirect).getByRole('button', { name: 'Go to Atlassian personal' }));
    expect(nav.push).toHaveBeenCalledWith(
      '/workspace/connectors/personal/?connectorType=Jira%20personal'
    );
    expect(useConnectorsStore.getState().isPanelOpen).toBe(false);
  });

  it('opens setup straight away for a connector without that requirement', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-Slack');

    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));

    expect(await screen.findByRole('dialog', { name: 'Set up Slack' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Admin access required' })).toBeNull();
  });
});

describe('Team connectors page: connectors whose data the whole organization can search', () => {
  const warningName = 'Everyone in your organization will see this data';

  it('warns before setup and points at the personal connector', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-S3');

    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));

    const dialog = await screen.findByRole('dialog', { name: warningName });
    expect(within(dialog).getByText(/Community Edition, the S3 connector does not apply permissions/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set up personal connector' }));
    expect(nav.push).toHaveBeenCalledWith('/workspace/connectors/personal/?connectorType=S3');
    expect(useConnectorsStore.getState().isPanelOpen).toBe(false);
  });

  it('starts team setup when the admin continues', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-S3');
    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));
    const dialog = await screen.findByRole('dialog', { name: warningName });

    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue with team connector' }));

    expect(await screen.findByRole('dialog', { name: 'Set up S3' })).toBeTruthy();
  });

  it('offers no personal connector for a team-only connector', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-Team Only DB');
    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));

    const dialog = await screen.findByRole('dialog', { name: warningName });
    expect(within(dialog).getByText(/available only as a team connector/)).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Set up personal connector' })).toBeNull();
  });

  it('does not warn for other connectors', async () => {
    renderInTheme(<TeamConnectorsPage />);
    const card = await screen.findByTestId('connector-card-Slack');
    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));

    expect(await screen.findByRole('dialog', { name: 'Set up Slack' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: warningName })).toBeNull();
  });
});

describe('Team connectors page: reads it can reuse', () => {
  const scheduled = makeConfig({
    config: { auth: {}, sync: { selectedStrategy: 'SCHEDULED', scheduledConfig: { intervalMinutes: 60 } }, filters: {} },
  });

  function openTypePage(readConfig: () => Promise<unknown> = async () => scheduled) {
    nav.params = new URLSearchParams('connectorType=Jira');
    api.getActiveConnectors.mockResolvedValue({ connectors: [makeInstance()] });
    api.getConnectorConfig.mockImplementation(readConfig);
    return renderInTheme(<TeamConnectorsPage />);
  }

  it('reads the team list once for a remount within a minute', async () => {
    const first = renderInTheme(<TeamConnectorsPage />);
    await screen.findByTestId('connector-card-Jira');
    first.unmount();

    renderInTheme(<TeamConnectorsPage />);
    await screen.findByTestId('connector-card-Jira');

    expect(api.getActiveConnectors).toHaveBeenCalledTimes(1);
  });

  it('shows the team list again after the personal page put its own list in the store', async () => {
    const teamInstance = makeInstance({ _key: 'team-1' });
    api.getActiveConnectors.mockResolvedValue({ connectors: [teamInstance] });
    const first = renderInTheme(<TeamConnectorsPage />);
    await waitFor(() => expect(useConnectorsStore.getState().activeConnectors.map((c) => c._key)).toEqual(['team-1']));
    first.unmount();
    useConnectorsStore.setState({ activeConnectors: [makeInstance({ _key: 'personal-1' })] });

    renderInTheme(<TeamConnectorsPage />);

    await waitFor(() => expect(useConnectorsStore.getState().activeConnectors.map((c) => c._key)).toEqual(['team-1']));
    expect(api.getActiveConnectors).toHaveBeenCalledTimes(1);
  });

  it('reads each instance config once, and not again when the type page reopens within a minute', async () => {
    const first = openTypePage();
    expect(await screen.findByText('Scheduled')).toBeTruthy();
    expect(api.getConnectorConfig).toHaveBeenCalledTimes(1);
    first.unmount();

    openTypePage();

    expect(await screen.findByText('Scheduled')).toBeTruthy();
    expect(api.getConnectorConfig).toHaveBeenCalledTimes(1);
  });

  it('makes no request when going back to the list', async () => {
    openTypePage();
    await screen.findByText('Scheduled');
    for (const fn of Object.values(api)) fn.mockClear();

    fireEvent.click(screen.getByRole('button', { name: 'Connectors' }));
    await act(async () => {});

    expect(nav.push).toHaveBeenCalledWith('/workspace/connectors/team/');
    expect(api.getRegistryConnectors).not.toHaveBeenCalled();
    expect(api.getActiveConnectors).not.toHaveBeenCalled();
    expect(api.getConnectorConfig).not.toHaveBeenCalled();
  });

  it('keeps configs that arrive after the admin left, so returning within a minute shows them', async () => {
    let answer: (config: unknown) => void = () => {};
    nav.params = new URLSearchParams('connectorType=Jira');
    api.getActiveConnectors.mockResolvedValue({ connectors: [makeInstance()] });
    api.getConnectorConfig.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    const first = renderInTheme(<TeamConnectorsPage />);
    await waitFor(() => expect(api.getConnectorConfig).toHaveBeenCalledTimes(1));

    first.unmount();
    useConnectorsStore.setState({ activeConnectors: [makeInstance({ _key: 'personal-1' })] });
    await act(async () => answer(scheduled));

    openTypePage();
    expect(await screen.findByText('Scheduled')).toBeTruthy();
    expect(api.getConnectorConfig).toHaveBeenCalledTimes(1);
  });

  it('does not read configs of the personal list a deep link finds in the store', async () => {
    useConnectorsStore.setState({ activeConnectors: [makeInstance({ _key: 'personal-1' })] });

    openTypePage();
    await screen.findByText('Scheduled');

    expect(api.getConnectorConfig).toHaveBeenCalledWith('conn-1');
    expect(api.getConnectorConfig).not.toHaveBeenCalledWith('personal-1');
  });

  it('reads the config of a new instance when the list gains one', async () => {
    openTypePage();
    await screen.findByText('Scheduled');

    act(() =>
      writeSwrKey(ACTIVE_TEAM_CONNECTORS_KEY, { connectors: [makeInstance(), makeInstance({ _key: 'conn-2', name: 'Jira (Ops)' })] }),
    );

    await waitFor(() => expect(api.getConnectorConfig).toHaveBeenCalledWith('conn-2'));
  });

  it('reads no config for an instance just deleted here that the server still lists', async () => {
    openTypePage();
    await screen.findByText('Scheduled');
    act(() => useConnectorsStore.getState().removeConnectorInstance('conn-2'));

    act(() =>
      writeSwrKey(ACTIVE_TEAM_CONNECTORS_KEY, { connectors: [makeInstance(), makeInstance({ _key: 'conn-2', name: 'Jira (Ops)' })] }),
    );
    await act(async () => {});

    expect(api.getConnectorConfig).not.toHaveBeenCalledWith('conn-2');
  });

  it('lets a failed list read during Refresh all raise its usual error', async () => {
    openTypePage();
    await screen.findByText('Jira (Engineering)');
    api.getActiveConnectors.mockClear();
    api.getConnectorInstance.mockResolvedValue(makeInstance());

    fireEvent.click(screen.getByRole('button', { name: /Refresh all/ }));

    await waitFor(() => expect(api.getActiveConnectors).toHaveBeenCalledWith('team'));
  });

  it('shares one list request between the page and its demo check', async () => {
    function DemoCheck() {
      useDemoDataActive();
      return null;
    }
    renderInTheme(
      <>
        <TeamConnectorsPage />
        <DemoCheck />
      </>,
    );
    await screen.findByTestId('connector-card-Jira');

    expect(api.getActiveConnectors).toHaveBeenCalledTimes(1);
  });

  it('keeps what Refresh all read for the next visit within a minute', async () => {
    openTypePage();
    await screen.findByText('Jira (Engineering)');
    api.getActiveConnectors.mockResolvedValue({ connectors: [makeInstance({ name: 'Jira (Renamed)' })] });
    api.getConnectorInstance.mockResolvedValue(makeInstance({ name: 'Jira (Renamed)' }));

    fireEvent.click(screen.getByRole('button', { name: /Refresh all/ }));
    await screen.findByText('Jira (Renamed)');
    cleanup();
    useConnectorsStore.setState({ activeConnectors: [] });

    renderInTheme(<TeamConnectorsPage />);

    await waitFor(() => expect(useConnectorsStore.getState().activeConnectors[0]?.name).toBe('Jira (Renamed)'));
  });

  it('after a minute, re-reads the list and configs in the background, keeping the cards on screen', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const first = openTypePage();
      await screen.findByText('Scheduled');
      first.unmount();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(61_000);
      });
      let answer: ((config: unknown) => void) | null = null;
      openTypePage(() => new Promise((resolve) => { answer = resolve; }));

      await waitFor(() => expect(api.getConnectorConfig).toHaveBeenCalledTimes(2));
      expect(api.getActiveConnectors).toHaveBeenCalledTimes(2);
      // The re-read is still in flight: the cards stay, with no loading skeleton.
      expect(answer).not.toBeNull();
      expect(screen.getByText('Scheduled')).toBeTruthy();
      expect(screen.queryByRole('status')).toBeNull();
      await act(async () => answer!(scheduled));
    } finally {
      vi.useRealTimers();
    }
  });

  it('reads the configs again after a catalog refresh (a save, a new instance, a sign-in)', async () => {
    openTypePage();
    await screen.findByText('Scheduled');

    act(() => useConnectorsStore.getState().bumpCatalogRefresh());

    await waitFor(() => expect(api.getConnectorConfig).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.getActiveConnectors).toHaveBeenCalledTimes(2));
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

  async function confirmDisable() {
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Disable sync' }));
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
    await confirmDisable();

    await waitFor(() => expect(api.toggleConnector).toHaveBeenCalledWith('conn-1', 'sync'));
    await waitFor(() => expect(toasts().map((t) => t.title)).toContain('Connector sync disabled'));
  });

  it('says it could not update the connector when the toggle fails outside the API', async () => {
    openTypePage(makeInstance({ isActive: true }));
    api.toggleConnector.mockRejectedValue(new Error('socket closed'));
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    fireEvent.click(toggle);
    await confirmDisable();

    await waitFor(() => expect(toasts().map((t) => t.title)).toContain('Could not update connector'));
    expect(toasts().map((t) => t.title)).not.toContain('Connector sync disabled');
  });

  it('leaves the message to the API layer when the server refused the toggle', async () => {
    openTypePage(makeInstance({ isActive: true }));
    api.toggleConnector.mockRejectedValue({ type: 'AUTHORIZATION_ERROR', message: 'Forbidden', statusCode: 403 });
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    fireEvent.click(toggle);
    await confirmDisable();

    await waitFor(() => expect(api.toggleConnector).toHaveBeenCalled());
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
    expect(toasts()).toEqual([]);
  });

  it('re-reads the connector after a failed toggle so the switch shows the real state', async () => {
    openTypePage(makeInstance({ isActive: false }));
    api.toggleConnector.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down', statusCode: 500 });
    const toggle = await screen.findByRole('switch');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
    api.getConnectorInstance.mockClear();

    fireEvent.click(toggle);

    await waitFor(() => expect(api.getConnectorInstance).toHaveBeenCalledWith('conn-1'));
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    expect(toasts().map((t) => t.title)).not.toContain('Connector sync enabled');
  });
});
