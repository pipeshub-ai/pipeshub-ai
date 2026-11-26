/**
 * Manage configuration opens a form whose save the server refuses without the
 * configure permission, so the button locks instead of opening it.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const permissions = vi.hoisted(() => ({ denied: new Set<string>() }));
vi.mock('@/config', () => ({
  PermissionLockIcon: () => <span data-testid="permission-lock" />,
  useUserPermission: (key: string) => !permissions.denied.has(key),
}));
vi.mock('next/navigation', () => ({ usePathname: () => '/workspace/connectors/personal' }));
vi.mock('../../../api', () => ({ ConnectorsApi: {} }));
vi.mock('../../../utils/fetch-instance-stats', () => ({ fetchInstanceStats: vi.fn().mockResolvedValue(undefined) }));
vi.mock('../overview-tab', () => ({ OverviewTab: () => null }));
vi.mock('../settings-tab', () => ({ SettingsTab: () => null }));
vi.mock('../tables-tab', () => ({ TablesTab: () => null }));

import { InstanceManagementPanel } from '..';
import { useConnectorsStore } from '../../../store';
import { installDomShims, makeInstance, renderInTheme } from '../../../__tests__/fixtures';

function openPanel() {
  const instance = makeInstance();
  const openPanelSpy = vi.fn();
  act(() => {
    useConnectorsStore.setState({
      isInstancePanelOpen: true,
      selectedInstance: instance,
      instances: [instance],
      instancePanelTab: 'overview',
      openPanel: openPanelSpy,
    } as never);
  });
  renderInTheme(<InstanceManagementPanel />);
  return { openPanelSpy };
}

const manageButton = () =>
  screen.getByRole('button', { name: /Manage configuration/ }) as HTMLButtonElement;

beforeEach(() => {
  installDomShims();
  permissions.denied = new Set();
});
afterEach(() => {
  cleanup();
  act(() => useConnectorsStore.setState({ isInstancePanelOpen: false, selectedInstance: null } as never));
});

describe('InstanceManagementPanel: Manage configuration', () => {
  it('opens the configuration with the configure permission', () => {
    const { openPanelSpy } = openPanel();

    expect(manageButton().disabled).toBe(false);
    fireEvent.click(manageButton());
    expect(openPanelSpy).toHaveBeenCalledOnce();
  });

  it('is locked without the configure permission and opens nothing', () => {
    permissions.denied = new Set(['configureConnector']);
    const { openPanelSpy } = openPanel();

    expect(manageButton().disabled).toBe(true);
    expect(screen.getByTestId('permission-lock')).toBeTruthy();
    fireEvent.click(manageButton());
    expect(openPanelSpy).not.toHaveBeenCalled();
  });
});
