import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { User } from '@/app/(main)/workspace/users/types';

vi.mock('@/config', () => ({ PermissionLockIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => null }));
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

import { SettingsTab } from '../instance-panel/settings-tab';
import { installDomShims, makeInstance, renderInTheme } from '../../__tests__/fixtures';

const CREATOR = '507f1f77bcf86cd799439011';

beforeEach(() => {
  installDomShims();
  directory.asked = [];
  directory.user = null;
});
afterEach(() => cleanup());

describe('SettingsTab: who created the instance', () => {
  it('shows the creator name and picture from the directory', () => {
    directory.user = {
      id: 'u1', userId: CREATOR, name: 'Ada Lovelace', hasLoggedIn: true, isActive: true,
      profilePicture: 'data:image/png;base64,AAAA',
    };
    renderInTheme(<SettingsTab instance={makeInstance({ createdBy: CREATOR })} />);

    expect(screen.getByText('Ada Lovelace')).toBeTruthy();
    expect(screen.getByTestId('avatar').getAttribute('data-src')).toBe('data:image/png;base64,AAAA');
    expect(directory.asked).toContain(CREATOR);
  });

  it('falls back to the name and avatar the instance carries when the directory has no such user', () => {
    renderInTheme(
      <SettingsTab
        instance={makeInstance({ createdBy: CREATOR, enabledBy: { name: 'Grace Hopper', avatar: '/grace.png' } })}
      />,
    );

    expect(screen.getByText('Grace Hopper')).toBeTruthy();
    expect(screen.getByTestId('avatar').getAttribute('data-src')).toBe('/grace.png');
  });
});
