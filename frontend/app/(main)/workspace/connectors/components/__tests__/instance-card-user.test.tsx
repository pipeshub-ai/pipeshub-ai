import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { User } from '@/app/(main)/workspace/users/types';

vi.mock('../../api', () => ({ ConnectorsApi: {} }));
vi.mock('@/config', () => ({ PermissionLockIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({
  ConnectorIcon: ({ type }: { type: string }) => <span data-testid="connector-logo">{type}</span>,
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

const PICTURE = 'data:image/png;base64,AAAA';

beforeEach(() => {
  installDomShims();
  directory.asked = [];
  directory.user = {
    id: 'u1',
    userId: '507f1f77bcf86cd799439011',
    name: 'Ada Lovelace',
    email: 'ada@acme.test',
    hasLoggedIn: true,
    isActive: true,
    profilePicture: PICTURE,
  };
});
afterEach(() => cleanup());

describe('InstanceCard: who enabled it', () => {
  it('shows the short name of the user who last updated the instance', () => {
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: '507f1f77bcf86cd799439011' })} />);

    expect(screen.getByText('Ada L')).toBeTruthy();
    expect(directory.asked).toContain('507f1f77bcf86cd799439011');
  });

  it('shows a dash when the user is unknown', () => {
    directory.user = null;
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: '507f1f77bcf86cd799439011' })} />);

    expect(screen.queryByText('Ada L')).toBeNull();
  });

  it('keeps the connector logo as the card icon, never the user photo', () => {
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: '507f1f77bcf86cd799439011' })} />);

    expect(screen.getByTestId('connector-logo')).toBeTruthy();
    expect(document.querySelector(`img[src="${PICTURE}"][width="32"]`)).toBeNull();
  });

  it('never points an image at the per-user picture route, which does not exist', () => {
    renderInTheme(<InstanceCard instance={makeInstance({ updatedBy: '507f1f77bcf86cd799439011' })} />);

    expect(document.querySelector('img[src*="/dp"]')).toBeNull();
  });
});
