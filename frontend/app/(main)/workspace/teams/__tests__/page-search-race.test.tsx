import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import type { Team } from '../types';

function team(id: string, name: string): Team {
  return {
    id,
    orgId: '507f1f77bcf86cd799439012',
    name,
    description: null,
    canDelete: true,
    canEdit: true,
    canManageMembers: true,
    memberCount: 1,
    members: [],
    createdAtTimestamp: 1779792574728,
    updatedAtTimestamp: 1779792574728,
  } as unknown as Team;
}

type ListResult = { teams: Team[]; totalCount: number };
type Pending = { search: string | undefined; resolve: (r: ListResult) => void; reject: (e: Error) => void };

const pending: Pending[] = [];
const listTeams = vi.fn(
  (params?: { search?: string }) =>
    new Promise<ListResult>((resolve, reject) => {
      pending.push({ search: params?.search, resolve, reject });
    }),
);

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
  useToastStore: (selector: (s: { addToast: () => void }) => unknown) => selector({ addToast: vi.fn() }),
}));

vi.mock('../api', () => ({
  TeamsApi: { listTeams: (params?: { search?: string }) => listTeams(params) },
}));

vi.mock('../../users/api', () => ({
  UsersApi: { fetchMergedUsers: vi.fn(async () => ({ users: [], totalCount: 0 })) },
}));

vi.mock('../components', () => ({
  CreateTeamSidebar: () => null,
  TeamDetailSidebar: () => null,
}));

import TeamsPage from '../page';
import { useTeamsStore } from '../store';

function requestFor(search: string | undefined): Pending {
  const found = [...pending].reverse().find((p) => p.search === search);
  if (!found) throw new Error(`no list request with search=${String(search)}`);
  return found;
}

// R46LANE-FU-5: the search fires one list request per change with no debounce, so an older request's
// late answer must not replace the rows of the newest search.
describe('Teams page — search answers arriving out of order', () => {
  beforeEach(() => {
    pending.length = 0;
    listTeams.mockClear();
    useTeamsStore.setState({ searchQuery: '', page: 1, teams: [], totalCount: 0, error: null });
  });
  afterEach(() => cleanup());

  it('keeps the newest search rows when an older search answers last', async () => {
    render(
      <Theme>
        <TeamsPage />
      </Theme>,
    );
    await act(async () => {
      requestFor(undefined).resolve({ teams: [team('1', 'Alpha'), team('2', 'Beta')], totalCount: 2 });
    });
    expect(await screen.findByText('Alpha')).toBeTruthy();

    const input = screen.getByPlaceholderText(/Search/i);
    fireEvent.change(input, { target: { value: 'b' } });
    fireEvent.change(input, { target: { value: 'be' } });
    expect(listTeams).toHaveBeenCalledWith(expect.objectContaining({ search: 'be' }));

    await act(async () => {
      requestFor('be').resolve({ teams: [team('2', 'Beta')], totalCount: 1 });
    });
    await act(async () => {
      requestFor('b').resolve({ teams: [team('1', 'Alpha'), team('2', 'Beta')], totalCount: 2 });
    });

    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.queryByText('Alpha')).toBeNull();
  });

  it('restores the full list when the search is cleared before its answer lands', async () => {
    render(
      <Theme>
        <TeamsPage />
      </Theme>,
    );
    await act(async () => {
      requestFor(undefined).resolve({ teams: [team('1', 'Alpha'), team('2', 'Beta')], totalCount: 2 });
    });
    const input = screen.getByPlaceholderText(/Search/i);
    fireEvent.change(input, { target: { value: 'zzz' } });
    fireEvent.change(input, { target: { value: '' } });

    await act(async () => {
      [...pending].reverse().find((p) => p.search === undefined)!.resolve({
        teams: [team('1', 'Alpha'), team('2', 'Beta')],
        totalCount: 2,
      });
    });
    await act(async () => {
      requestFor('zzz').resolve({ teams: [], totalCount: 0 });
    });

    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
  });

  it('ignores an older request failing after a newer one answered', async () => {
    render(
      <Theme>
        <TeamsPage />
      </Theme>,
    );
    await act(async () => {
      requestFor(undefined).resolve({ teams: [team('1', 'Alpha')], totalCount: 1 });
    });
    const input = screen.getByPlaceholderText(/Search/i);
    fireEvent.change(input, { target: { value: 'g' } });
    fireEvent.change(input, { target: { value: 'ga' } });
    await act(async () => {
      requestFor('ga').resolve({ teams: [team('3', 'Gamma')], totalCount: 1 });
    });
    await act(async () => {
      requestFor('g').reject(new Error('boom'));
    });

    expect(screen.getByText('Gamma')).toBeTruthy();
    expect(useTeamsStore.getState().error).toBeNull();
  });
});
