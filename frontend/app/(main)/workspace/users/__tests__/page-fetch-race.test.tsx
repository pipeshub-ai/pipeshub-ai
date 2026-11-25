import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import type { User } from '../types';

function user(id: string, name: string): User {
  return { id, userId: id, name, email: `${name.toLowerCase()}@example.com`, hasLoggedIn: true, isActive: true } as User;
}

type ListResult = { users: User[]; totalCount: number };
type Pending = { search: string | undefined; resolve: (r: ListResult) => void; reject: (e: Error) => void };

const pending: Pending[] = [];
const fetchMergedUsers = vi.fn(
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
  UsersPageHeaderActions: () => null,
  useMemberAccountActions: () => ({ action: () => false, dialog: null }),
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
  UsersApi: { fetchMergedUsers: (params?: { search?: string }) => fetchMergedUsers(params) },
}));

vi.mock('../../mail/api', () => ({ SmtpApi: { isConfigured: vi.fn(async () => true) } }));
vi.mock('../../profile/api', () => ({ ProfileApi: {} }));

vi.mock('../components', () => ({
  InviteUsersSidebar: () => null,
  UserProfileSidebar: () => null,
}));


import UsersPage from '../page';
import { useUsersStore } from '../store';

function requestFor(search: string | undefined): Pending {
  const found = [...pending].reverse().find((p) => p.search === search);
  if (!found) throw new Error(`no list request with search=${String(search)}`);
  return found;
}

function names(): string[] {
  return useUsersStore.getState().users.map((u) => u.name ?? '');
}

async function searchFor(query: string): Promise<void> {
  await act(async () => {
    useUsersStore.getState().setSearchQuery(query);
  });
}

// R50-JFIX: every search change sends its own list request; an older request's late answer must not replace the
// rows, the error or the loading flag of the newest one.
describe('Users page — list answers arriving out of order', () => {
  beforeEach(() => {
    pending.length = 0;
    fetchMergedUsers.mockClear();
    useUsersStore.setState({ searchQuery: '', page: 1, users: [], totalCount: 0, error: null, isLoading: false });
  });
  afterEach(() => cleanup());

  async function rendered(): Promise<void> {
    render(
      <Theme>
        <UsersPage />
      </Theme>,
    );
    await act(async () => {
      requestFor(undefined).resolve({ users: [user('1', 'Alice'), user('2', 'Bob')], totalCount: 2 });
    });
    expect(names()).toEqual(['Alice', 'Bob']);
  }

  it('keeps the newest search rows when an older search answers last', async () => {
    await rendered();
    await searchFor('b');
    await searchFor('bo');
    await act(async () => {
      requestFor('bo').resolve({ users: [user('2', 'Bob')], totalCount: 1 });
    });
    await act(async () => {
      requestFor('b').resolve({ users: [user('1', 'Alice'), user('2', 'Bob')], totalCount: 2 });
    });
    expect(names()).toEqual(['Bob']);
    expect(useUsersStore.getState().totalCount).toBe(1);
  });

  it('ignores an older request failing after a newer one answered, and leaves loading to the newest', async () => {
    await rendered();
    await searchFor('c');
    await searchFor('ca');
    await act(async () => {
      requestFor('c').reject(new Error('boom'));
    });
    expect(useUsersStore.getState().error).toBeNull();
    expect(useUsersStore.getState().isLoading).toBe(true);
    await act(async () => {
      requestFor('ca').resolve({ users: [user('3', 'Carol')], totalCount: 1 });
    });
    expect(names()).toEqual(['Carol']);
    expect(useUsersStore.getState().isLoading).toBe(false);
  });
});
