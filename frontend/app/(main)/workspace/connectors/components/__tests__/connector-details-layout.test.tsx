import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('../../api', () => ({ ConnectorsApi: {} }));
vi.mock('@/config', () => ({ PermissionLockIcon: () => <span>locked</span> }));
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

import { ConnectorDetailsLayout } from '../connector-details-layout';
import type { ConnectorInstance } from '../../types';
import {
  installDomShims,
  renderInTheme,
  makeConnector,
  makeInstance,
  makeConfig,
  toasts,
  clearToasts,
} from '../../__tests__/fixtures';

type Props = React.ComponentProps<typeof ConnectorDetailsLayout>;

function renderLayout(overrides: Partial<Props> = {}) {
  const props: Props = {
    connector: makeConnector({ isConfigured: true }),
    scope: 'team',
    scopeLabel: 'Connectors',
    instances: [],
    isLoading: false,
    onBack: vi.fn(),
    onAddInstance: vi.fn(),
    onManageInstance: vi.fn(),
    onToggleSyncActive: vi.fn(),
    onInstanceChevron: vi.fn(),
    ...overrides,
  };
  renderInTheme(<ConnectorDetailsLayout {...props} />);
  return props;
}

/** The nearest block around an instance name that also holds its sync switch. */
function card(name: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(name);
  while (el && !el.querySelector('[role="switch"]')) el = el.parentElement;
  if (!el) throw new Error(`No instance card for "${name}"`);
  return el;
}

beforeEach(() => {
  installDomShims();
  clearToasts();
});
afterEach(() => cleanup());

describe('ConnectorDetailsLayout: page states', () => {
  it('shows a loader while instances load', () => {
    renderLayout({ isLoading: true });
    expect(screen.getByRole('status').textContent).toBe('Loading instances…');
    expect(screen.queryByText('No instances configured yet')).toBeNull();
  });

  it('says when there are no instances yet', () => {
    renderLayout();
    expect(screen.getByText('No instances configured yet')).toBeTruthy();
  });

  it('names the connector and goes back to the list from the breadcrumb', () => {
    const props = renderLayout();
    expect(screen.getByRole('heading', { name: 'Jira' })).toBeTruthy();
    expect(screen.getByText('Sync issues and comments')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Connectors' }));
    expect(props.onBack).toHaveBeenCalled();
  });

  it('opens the documentation from a button named for it', () => {
    const onOpenDocs = vi.fn();
    renderLayout({ onOpenDocs });
    fireEvent.click(screen.getByRole('button', { name: 'Documentation' }));
    expect(onOpenDocs).toHaveBeenCalled();
  });

  it('adds another instance', () => {
    const props = renderLayout();
    fireEvent.click(screen.getByRole('button', { name: /Add Another Instance/ }));
    expect(props.onAddInstance).toHaveBeenCalled();
  });

  it('shows a lock and does nothing when the user may not create instances', () => {
    const props = renderLayout({ createPermissionDenied: true });
    const add = screen.getByRole('button', { name: /Add Another Instance/ });
    expect(within(add).getByText('locked')).toBeTruthy();
    fireEvent.click(add);
    expect(props.onAddInstance).not.toHaveBeenCalled();
  });

  it('links to the personal connector when the team one needs admin access', () => {
    renderLayout({
      connector: makeConnector({
        connectorInfo: 'Team setup needs admin access in Jira.',
        personalConnectorType: 'Jira Personal',
      }),
    });
    const link = screen.getByRole('link', { name: 'Use Jira Personal instead →' });
    expect(link.getAttribute('href')).toBe(
      '/workspace/connectors/personal/?connectorType=Jira%20Personal'
    );
  });
});

describe('ConnectorDetailsLayout: instance status', () => {
  it('shows each instance with its setup status and sync strategy', () => {
    const instance = makeInstance();
    renderLayout({
      instances: [instance],
      instanceConfigs: {
        'conn-1': makeConfig({
          config: {
            auth: {},
            sync: { selectedStrategy: 'SCHEDULED', scheduledConfig: { intervalMinutes: 60 } },
            filters: {},
          },
        }),
      },
    });

    const c = card('Jira (Engineering)');
    expect(within(c).getByText('Configuration')).toBeTruthy();
    expect(within(c).getByText('Complete')).toBeTruthy();
    expect(within(c).getByText('Scheduled')).toBeTruthy();
    expect(within(c).getByText('Every 1 Hour')).toBeTruthy();
  });

  it('marks an unfinished instance and explains why sync cannot be turned on', () => {
    renderLayout({ instances: [makeInstance({ isConfigured: false })] });

    const c = card('Jira (Engineering)');
    expect(within(c).getByText('Incomplete')).toBeTruthy();
    expect(within(c).getByRole('switch').hasAttribute('disabled')).toBe(true);
  });

  it('shows that a sync is running', () => {
    renderLayout({ instances: [makeInstance({ isActive: true, status: 'SYNCING' })] });
    expect(screen.getByText('Sync in progress')).toBeTruthy();
  });

  it('locks sync and hides the sync buttons while an instance is being removed', () => {
    renderLayout({ instances: [makeInstance({ isActive: true, status: 'DELETING' })] });
    expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(true);
    expect(screen.queryByRole('button', { name: /^Sync$/ })).toBeNull();
  });

  it('asks the user to sign in when an OAuth instance lost its authentication', () => {
    const instance = makeInstance({ isActive: true, authType: 'OAUTH', isAuthenticated: false });
    const props = renderLayout({
      instances: [instance],
      instanceConfigs: { 'conn-1': makeConfig({ authType: 'OAUTH', isAuthenticated: false }) },
    });

    expect(screen.getByText('Needs auth')).toBeTruthy();
    expect(screen.getByText('Authenticate Personal Account')).toBeTruthy();
    expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: /^Connect$/ }));
    expect(props.onManageInstance).toHaveBeenCalledWith(instance);
  });
});

describe('ConnectorDetailsLayout: enabling and disabling sync', () => {
  it('reflects whether sync is on', () => {
    renderLayout({
      instances: [
        makeInstance({ _key: 'a', name: 'On', isActive: true }),
        makeInstance({ _key: 'b', name: 'Off', isActive: false }),
      ],
    });
    expect(within(card('On')).getByRole('switch').getAttribute('aria-checked')).toBe('true');
    expect(within(card('Off')).getByRole('switch').getAttribute('aria-checked')).toBe('false');
  });

  it('turns sync on for a ready instance', async () => {
    const instance = makeInstance({ isActive: false });
    const props = renderLayout({ instances: [instance] });

    fireEvent.click(screen.getByRole('switch'));

    await waitFor(() => expect(props.onToggleSyncActive).toHaveBeenCalledWith(instance));
  });

  it('ignores extra clicks while a toggle is still in flight', async () => {
    let finish: () => void = () => {};
    const onToggleSyncActive = vi.fn(() => new Promise<void>((r) => (finish = r)));
    renderLayout({ instances: [makeInstance({ isActive: true })], onToggleSyncActive });

    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(true));
    fireEvent.click(screen.getByRole('switch'));

    expect(onToggleSyncActive).toHaveBeenCalledTimes(1);
    finish();
    await waitFor(() => expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(false));
  });

  it('offers the sync buttons only while sync is on', () => {
    renderLayout({
      instances: [
        makeInstance({ _key: 'a', name: 'On', isActive: true }),
        makeInstance({ _key: 'b', name: 'Off', isActive: false }),
      ],
    });
    expect(within(card('On')).getByRole('button', { name: /^Sync$/ })).toBeTruthy();
    expect(within(card('On')).getByRole('button', { name: /Full sync/ })).toBeTruthy();
    expect(within(card('Off')).queryByRole('button', { name: /^Sync$/ })).toBeNull();
  });

  it('opens the management panel from the card', () => {
    const instance = makeInstance();
    const props = renderLayout({ instances: [instance], onInstanceChevron: vi.fn() });
    const buttons = within(card('Jira (Engineering)')).getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    expect(props.onInstanceChevron).toHaveBeenCalledWith(instance);
  });
});

describe('ConnectorDetailsLayout: refreshing', () => {
  it('refreshes one instance and reports a failure in plain words', async () => {
    const onRefreshInstance = vi.fn<(i: ConnectorInstance) => Promise<void>>().mockRejectedValue(
      new Error('network')
    );
    renderLayout({ instances: [makeInstance()], onRefreshInstance });

    fireEvent.click(screen.getByRole('button', { name: 'Refresh instance details' }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      title: 'Failed to refresh connector instances',
    });
    expect(screen.getByRole('button', { name: 'Refresh instance details' }).hasAttribute('disabled')).toBe(
      false
    );
  });

  it('refreshes every instance and disables the button meanwhile', async () => {
    let finish: () => void = () => {};
    const onRefreshAll = vi.fn(() => new Promise<void>((r) => (finish = r)));
    renderLayout({ instances: [makeInstance()], onRefreshAll, onRefreshInstance: vi.fn() });

    fireEvent.click(screen.getByRole('button', { name: /Refresh all/ }));

    expect(onRefreshAll).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Refresh instance details' }).hasAttribute('disabled')
      ).toBe(true)
    );
    finish();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Refresh instance details' }).hasAttribute('disabled')
      ).toBe(false)
    );
  });

  it('hides Refresh all when there is nothing to refresh', () => {
    renderLayout({ onRefreshAll: vi.fn() });
    expect(screen.queryByRole('button', { name: /Refresh all/ })).toBeNull();
  });
});
