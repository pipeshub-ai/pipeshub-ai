import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

// Only org admins may invite, so a member must see no invite controls: no
// Invite button, no invite panel (even from a ?panel=invite link), no resend.

let searchParams = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useSearchParams: () => searchParams,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock('@/config', () => ({
  useAuthStore: (select: (s: { user: { id: string } }) => unknown) => select({ user: { id: 'me' } }),
  UsersPageHeaderActions: () => null,
  InviteUsersSidebar: () => <div data-testid="invite-panel" />,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/user-profile-sidebar', () => ({ UserProfileSidebar: () => null }));
vi.mock('../../mail/api', () => ({ SmtpApi: { isConfigured: vi.fn().mockResolvedValue(true) } }));
vi.mock('../api', () => ({
  UsersApi: {
    fetchMergedUsers: vi.fn().mockResolvedValue({
      totalCount: 1,
      users: [
        { id: 'u1', userId: 'u1', name: 'Pending Pat', email: 'pat@example.com', hasLoggedIn: false, isActive: true, role: 'Member' },
      ],
    }),
    resendInvite: vi.fn(),
  },
}));

import UsersPage from '../page';
import { useUserStore } from '@/lib/store/user-store';
import { useUsersStore } from '../store';

function signInAs(isAdmin: boolean) {
  useUserStore.getState().setProfile({
    userId: 'me',
    firstName: 'Me',
    lastName: 'Tester',
    fullName: 'Me Tester',
    email: 'me@example.com',
    isAdmin,
    avatarUrl: null,
    hasLoggedIn: true,
  });
  useUserStore.getState().setInitialized(true);
}

const renderPage = () => render(<Theme><UsersPage /></Theme>);

beforeEach(() => {
  searchParams = new URLSearchParams();
});

afterEach(() => {
  cleanup();
  useUsersStore.getState().closeInvitePanel();
});

describe('Users page invite controls', () => {
  it('shows an admin the Invite button and the invite panel', async () => {
    signInAs(true);
    renderPage();

    expect(await screen.findByRole('button', { name: 'Invite' })).toBeTruthy();
    expect(screen.getByTestId('invite-panel')).toBeTruthy();
  });

  it('shows a member no Invite button and no invite panel', async () => {
    signInAs(false);
    renderPage();

    await screen.findByText('Pending Pat');
    expect(screen.queryByRole('button', { name: 'Invite' })).toBeNull();
    expect(screen.queryByTestId('invite-panel')).toBeNull();
  });

  it('does not open the invite panel for a member who follows a ?panel=invite link', async () => {
    signInAs(false);
    searchParams = new URLSearchParams('panel=invite');
    renderPage();

    await screen.findByText('Pending Pat');
    await waitFor(() => expect(useUsersStore.getState().isInvitePanelOpen).toBe(false));
    expect(screen.queryByTestId('invite-panel')).toBeNull();
  });
});
