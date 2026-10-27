import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import type { Team } from '../types';

const TEAM: Team = {
  id: '550e8400-e29b-41d4-a716-446655440000',
  orgId: '507f1f77bcf86cd799439012',
  name: 'Platform Team',
  description: null,
  canDelete: true,
  canEdit: true,
  canManageMembers: true,
  memberCount: 1,
  members: [],
  createdAtTimestamp: 1779792574728,
  updatedAtTimestamp: 1779792574728,
} as unknown as Team;

const deleteTeam = vi.fn<(id: string) => Promise<void>>();
const listTeams = vi.fn(async () => ({ teams: [TEAM], totalCount: 1 }));
const addToast = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/config', () => ({
  useAuthStore: (selector: (s: { user: null }) => unknown) => selector({ user: null }),
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isProfileInitialized: boolean; profile: null }) => unknown) =>
    selector({ isAdmin: true, isProfileInitialized: true, profile: null }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  selectIsProfileInitialized: (s: { isProfileInitialized: boolean }) => s.isProfileInitialized,
}));

vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: typeof addToast }) => unknown) => selector({ addToast }),
}));

vi.mock('../api', () => ({
  TeamsApi: {
    listTeams: (...args: unknown[]) => listTeams(...(args as [])),
    deleteTeam: (id: string) => deleteTeam(id),
  },
}));

vi.mock('../../users/api', () => ({
  UsersApi: { fetchMergedUsers: vi.fn(async () => ({ users: [], totalCount: 0 })) },
}));

vi.mock('../components', () => ({
  CreateTeamSidebar: () => null,
  TeamDetailSidebar: () => null,
}));

// The real menu is a Radix popover; plain buttons keep the test on what the
// actions do rather than on how the popover opens.
vi.mock('../../components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components')>();
  return {
    ...actual,
    EntityRowActionMenu: ({ actions }: { actions: (false | { label: string; onClick?: () => void })[] }) => (
      <div>
        {actions.filter(Boolean).map((a) =>
          a ? (
            <button key={a.label} type="button" onClick={() => a.onClick?.()}>
              {`row:${a.label}`}
            </button>
          ) : null,
        )}
      </div>
    ),
  };
});

import TeamsPage from '../page';

function renderPage() {
  return render(
    <Theme>
      <TeamsPage />
    </Theme>,
  );
}

describe('Teams page — deleting a team from the row menu', () => {
  beforeEach(() => {
    deleteTeam.mockReset();
    deleteTeam.mockResolvedValue(undefined);
    listTeams.mockClear();
    addToast.mockClear();
  });
  afterEach(() => cleanup());

  it('asks for confirmation instead of deleting straight away', async () => {
    renderPage();
    fireEvent.click(await screen.findByText('row:Delete'));

    expect(await screen.findByText('Delete team?')).toBeTruthy();
    expect(screen.getByText(/"Platform Team" will be deleted/)).toBeTruthy();
    expect(deleteTeam).not.toHaveBeenCalled();
  });

  it('deletes nothing when the confirmation is cancelled', async () => {
    renderPage();
    fireEvent.click(await screen.findByText('row:Delete'));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Delete team?')).toBeNull());
    expect(deleteTeam).not.toHaveBeenCalled();
  });

  it('deletes the team once the confirmation is accepted', async () => {
    renderPage();
    fireEvent.click(await screen.findByText('row:Delete'));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete team' }));

    await waitFor(() => expect(deleteTeam).toHaveBeenCalledTimes(1));
    expect(deleteTeam).toHaveBeenCalledWith(TEAM.id);
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ variant: 'success', title: 'Team deleted' })),
    );
  });
});
