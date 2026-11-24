import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { User } from '@/app/(main)/workspace/users/types';

vi.mock('../../api', () => ({ ConnectorsApi: {} }));
const permissions = vi.hoisted(() => ({ denied: new Set<string>() }));
vi.mock('@/config', () => ({
  PermissionLockIcon: () => <span data-testid="permission-lock" />,
  useUserPermission: (key: string) => !permissions.denied.has(key),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({
  ConnectorIcon: ({ type }: { type: string }) => <span data-testid="connector-logo">{type}</span>,
}));
// Radix renders an avatar's <img> only once it loads, which never happens in jsdom.
vi.mock('@radix-ui/themes', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@radix-ui/themes')>()),
  Avatar: ({ src }: { src?: string }) => <span data-testid="avatar" data-src={src ?? ''} />,
}));
const directory = vi.hoisted(() => ({ user: null as User | null, asked: [] as unknown[] }));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({
  useUserDirectoryEntry: (id: unknown) => {
    directory.asked.push(id);
    return directory.user;
  },
}));

import { InstanceCard } from '../instance-card';
import { installDomShims, makeInstance, renderInTheme } from '../../__tests__/fixtures';

const USER_ID = '507f1f77bcf86cd799439011';
const PICTURE = 'data:image/png;base64,AAAA';

beforeEach(() => {
  installDomShims();
  permissions.denied = new Set();
  directory.asked = [];
  directory.user = {
    id: 'u1',
    userId: USER_ID,
    name: 'Ada Lovelace',
    email: 'ada@acme.test',
    hasLoggedIn: true,
    isActive: true,
    profilePicture: PICTURE,
  };
});
afterEach(() => cleanup());

describe('InstanceCard: who enabled it', () => {
  it('shows the short name and picture of the user who last updated the instance', () => {
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: USER_ID })} />);

    expect(screen.getByText('Ada L')).toBeTruthy();
    expect(screen.getByTestId('avatar').getAttribute('data-src')).toBe(PICTURE);
    expect(directory.asked).toContain(USER_ID);
  });

  it('shows no avatar, only a dash, when the user is unknown', () => {
    directory.user = null;
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: USER_ID })} />);

    expect(screen.queryByText('Ada L')).toBeNull();
    expect(screen.queryByTestId('avatar')).toBeNull();
  });

  it('leaves the avatar to its initials when the user has no picture, and never points it at /dp', () => {
    directory.user = { ...directory.user!, profilePicture: undefined };
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: USER_ID })} />);

    expect(screen.getByTestId('avatar').getAttribute('data-src')).toBe('');
  });

  it('keeps the connector logo as the icon of a personal instance, not the owner photo', () => {
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: USER_ID, scope: 'personal' })} />);

    expect(screen.getByTestId('connector-logo')).toBeTruthy();
    expect(screen.getAllByTestId('avatar')).toHaveLength(1);
  });
});

describe('InstanceCard: group permission locks', () => {
  it('lets the name be renamed and the sync switch be used when both permissions are on', () => {
    renderInTheme(<InstanceCard instance={makeInstance({ supportsSync: true })} onToggleSyncActive={vi.fn()} />);

    expect(screen.queryByTestId('permission-lock')).toBeNull();
    expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(false);
  });

  it('shows the name with a lock and opens no editor without configureConnector', () => {
    permissions.denied = new Set(['configureConnector']);
    renderInTheme(<InstanceCard instance={makeInstance({ supportsSync: true })} onToggleSyncActive={vi.fn()} />);

    expect(screen.getAllByTestId('permission-lock')).toHaveLength(1);
    screen.getByText('Jira (Engineering)').click();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('disables the sync switch with a lock without manageConnectorSync', () => {
    permissions.denied = new Set(['manageConnectorSync']);
    const onToggle = vi.fn();
    renderInTheme(<InstanceCard instance={makeInstance({ supportsSync: true })} onToggleSyncActive={onToggle} />);

    const toggle = screen.getByRole('switch');
    expect(toggle.hasAttribute('disabled')).toBe(true);
    expect(screen.getAllByTestId('permission-lock')).toHaveLength(1);
    toggle.click();
    expect(onToggle).not.toHaveBeenCalled();
  });
});

