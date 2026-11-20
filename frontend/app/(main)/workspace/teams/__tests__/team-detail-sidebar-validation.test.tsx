import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import type { Team } from '../types';
import { ErrorType } from '@/lib/api/api-error';

const TEAM = {
  id: '550e8400-e29b-41d4-a716-446655440000',
  orgId: '507f1f77bcf86cd799439012',
  name: 'Platform Team',
  description: 'Runs the platform',
  canDelete: true,
  canEdit: true,
  canManageMembers: true,
  memberCount: 1,
  members: [],
  createdAtTimestamp: 1779792574728,
  updatedAtTimestamp: 1779792574728,
} as unknown as Team;

const updateTeam = vi.fn();
const getTeam = vi.fn();
const addToast = vi.fn();

vi.mock('@/config', () => ({
  useAuthStore: (selector: (s: { user: null }) => unknown) => selector({ user: null }),
}));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { profile: null }) => unknown) => selector({ profile: null }),
}));
vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: typeof addToast }) => unknown) => selector({ addToast }),
}));
vi.mock('../api', () => ({
  TeamsApi: {
    updateTeam: (...a: unknown[]) => updateTeam(...a),
    getTeam: (...a: unknown[]) => getTeam(...a),
    getTeamUsers: vi.fn(async () => ({ members: [], totalCount: 0 })),
  },
}));
vi.mock('../../hooks/use-paginated-user-options', () => ({
  usePaginatedUserOptions: () => ({
    options: [],
    isLoading: false,
    hasMore: false,
    onSearch: vi.fn(),
    onLoadMore: vi.fn(),
  }),
}));
vi.mock('../../components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components')>();
  return { ...actual, PaginatedMembersList: () => null, SearchableCheckboxDropdown: () => null };
});

import { TeamDetailSidebar } from '../components/team-detail-sidebar';
import { useTeamsStore } from '../store';

const saveButton = () => screen.getByRole('button', { name: /^Save edits/i }) as HTMLButtonElement;

function openInEditMode() {
  act(() => {
    useTeamsStore.getState().openDetailPanel(TEAM);
    useTeamsStore.getState().enterEditMode();
  });
}

beforeEach(() => {
  updateTeam.mockReset();
  getTeam.mockReset();
  addToast.mockClear();
});
afterEach(() => {
  cleanup();
  act(() => useTeamsStore.getState().closeDetailPanel());
});

describe('Team edit panel validation', () => {
  it('disables Save Edits while the name is blank, so nothing is saved and no "Team updated" appears', async () => {
    render(
      <Theme>
        <TeamDetailSidebar />
      </Theme>,
    );
    openInEditMode();
    const name = (await screen.findByLabelText(/^Team name/i)) as HTMLInputElement;
    expect(saveButton().disabled).toBe(false);

    fireEvent.change(name, { target: { value: '   ' } });
    expect(saveButton().disabled).toBe(true);
    fireEvent.click(saveButton());
    expect(updateTeam).not.toHaveBeenCalled();
    expect(addToast).not.toHaveBeenCalled();

    fireEvent.blur(name);
    expect(screen.getByRole('alert').textContent).toBe('Enter a name.');
    expect(name.getAttribute('aria-invalid')).toBe('true');
  });

  it('disables Save Edits for a name over 100 characters, like a group name', async () => {
    render(
      <Theme>
        <TeamDetailSidebar />
      </Theme>,
    );
    openInEditMode();
    const name = await screen.findByLabelText(/^Team name/i);
    fireEvent.change(name, { target: { value: 'a'.repeat(101) } });
    fireEvent.blur(name);
    expect(saveButton().disabled).toBe(true);
    expect(screen.getByRole('alert').textContent).toBe('Name must be at most 100 characters.');
  });

  it('puts a server reason for the name on the field and shows no toast', async () => {
    updateTeam.mockRejectedValue({
      type: ErrorType.VALIDATION_ERROR,
      message: 'Name must be at most 100 characters.',
      statusCode: 400,
      fieldErrors: { name: 'That name is not allowed.' },
    });
    render(
      <Theme>
        <TeamDetailSidebar />
      </Theme>,
    );
    openInEditMode();
    const name = await screen.findByLabelText(/^Team name/i);
    fireEvent.change(name, { target: { value: 'Platform 2' } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('That name is not allowed.'));
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect(addToast).not.toHaveBeenCalled();
  });

  it('keeps one toast for a failure that names no field', async () => {
    updateTeam.mockRejectedValue({ type: ErrorType.SERVER_ERROR, message: 'Server error.', statusCode: 500 });
    render(
      <Theme>
        <TeamDetailSidebar />
      </Theme>,
    );
    openInEditMode();
    await screen.findByLabelText(/^Team name/i);
    fireEvent.click(saveButton());
    await waitFor(() => expect(addToast).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
