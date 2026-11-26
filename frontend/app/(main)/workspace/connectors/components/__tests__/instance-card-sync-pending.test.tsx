import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('../../api', () => ({ ConnectorsApi: {} }));
vi.mock('@/config', () => ({
  PermissionLockIcon: () => <span data-testid="permission-lock" />,
  useUserPermission: () => true,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({
  ConnectorIcon: ({ type }: { type: string }) => <span data-testid="connector-logo">{type}</span>,
}));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({ useUserDirectoryEntry: () => null }));

import { InstanceCard } from '../instance-card';
import { useConnectorsStore } from '../../store';
import { installDomShims, makeInstance, renderInTheme } from '../../__tests__/fixtures';

const syncSwitch = () => screen.getByRole('switch');

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
});
afterEach(() => cleanup());

describe('InstanceCard: sync turning on or off', () => {
  it('shows a spinner and "Turning on…" while sync is turning on', () => {
    const instance = makeInstance({ isActive: false });
    useConnectorsStore.getState().setSyncTogglePending(instance._key, 'on');
    renderInTheme(<InstanceCard instance={instance} onToggleSyncActive={vi.fn()} />);

    // Busy, not disabled: a disabled control drops keyboard focus mid-request.
    expect(syncSwitch().hasAttribute('disabled')).toBe(false);
    expect(syncSwitch().getAttribute('aria-disabled')).toBe('true');
    expect(syncSwitch().getAttribute('aria-busy')).toBe('true');
    expect(screen.getByText('Turning on…')).toBeTruthy();
  });

  it('ignores a click while sync is turning on', () => {
    const instance = makeInstance({ isActive: false });
    const onToggle = vi.fn();
    useConnectorsStore.getState().setSyncTogglePending(instance._key, 'on');
    renderInTheme(<InstanceCard instance={instance} onToggleSyncActive={onToggle} />);

    fireEvent.click(syncSwitch());
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('shows "Turning off…" while sync is turning off', () => {
    const instance = makeInstance({ isActive: true });
    useConnectorsStore.getState().setSyncTogglePending(instance._key, 'off');
    renderInTheme(<InstanceCard instance={instance} onToggleSyncActive={vi.fn()} />);

    expect(screen.getByText('Turning off…')).toBeTruthy();
  });

  it('reflects a change started somewhere else and clears when it ends', () => {
    const instance = makeInstance({ isActive: false });
    renderInTheme(<InstanceCard instance={instance} onToggleSyncActive={vi.fn()} />);
    expect(syncSwitch().hasAttribute('aria-disabled')).toBe(false);

    act(() => useConnectorsStore.getState().setSyncTogglePending(instance._key, 'on'));
    expect(syncSwitch().getAttribute('aria-disabled')).toBe('true');

    act(() => useConnectorsStore.getState().clearSyncTogglePending(instance._key));
    expect(syncSwitch().hasAttribute('aria-disabled')).toBe(false);
    expect(syncSwitch().hasAttribute('aria-busy')).toBe(false);
    expect(screen.queryByText('Turning on…')).toBeNull();
  });

  it('ignores another connector being pending', () => {
    const instance = makeInstance({ isActive: false });
    useConnectorsStore.getState().setSyncTogglePending('someone-else', 'on');
    renderInTheme(<InstanceCard instance={instance} onToggleSyncActive={vi.fn()} />);

    expect(syncSwitch().hasAttribute('aria-disabled')).toBe(false);
    expect(screen.queryByText('Turning on…')).toBeNull();
  });
});
