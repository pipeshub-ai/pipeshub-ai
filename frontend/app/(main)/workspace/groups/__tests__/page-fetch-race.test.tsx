import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import type { Group } from '../types';

function group(id: string, name: string): Group {
  return {
    _id: id,
    name,
    type: 'custom',
    orgId: '507f1f77bcf86cd799439012',
    userCount: 1,
    isDeleted: false,
    createdAt: '2026-11-01T00:00:00.000Z',
    updatedAt: '2026-11-01T00:00:00.000Z',
    slug: name.toLowerCase(),
  };
}

type ListResult = { groups: Group[]; totalCount: number };
type Pending = { search: string | undefined; resolve: (r: ListResult) => void; reject: (e: Error) => void };

const pending: Pending[] = [];
const listGroups = vi.fn(
  (params?: { search?: string }) =>
    new Promise<ListResult>((resolve, reject) => {
      pending.push({ search: params?.search, resolve, reject });
    }),
);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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
  GroupsApi: { listGroups: (params?: { search?: string }) => listGroups(params) },
}));

vi.mock('../components/create-group-sidebar', () => ({ CreateGroupSidebar: () => null }));
vi.mock('../components/group-detail-sidebar', () => ({ GroupDetailSidebar: () => null }));

import GroupsPage from '../groupsPage';
import { useGroupsStore } from '../store';

function requestFor(search: string | undefined): Pending {
  const found = [...pending].reverse().find((p) => p.search === search);
  if (!found) throw new Error(`no list request with search=${String(search)}`);
  return found;
}

function names(): string[] {
  return useGroupsStore.getState().groups.map((g) => g.name);
}

async function searchFor(query: string): Promise<void> {
  await act(async () => {
    useGroupsStore.getState().setSearchQuery(query);
  });
}

// R50-JFIX: every search change sends its own list request; an older request's late answer must not replace the
// rows, the error or the loading flag of the newest one.
describe('Groups page — list answers arriving out of order', () => {
  beforeEach(() => {
    pending.length = 0;
    listGroups.mockClear();
    useGroupsStore.setState({ searchQuery: '', page: 1, groups: [], totalCount: 0, error: null, isLoading: false });
  });
  afterEach(() => cleanup());

  async function rendered(): Promise<void> {
    render(
      <Theme>
        <GroupsPage />
      </Theme>,
    );
    await act(async () => {
      requestFor(undefined).resolve({ groups: [group('1', 'Admins'), group('2', 'Builders')], totalCount: 2 });
    });
    expect(names()).toEqual(['Admins', 'Builders']);
  }

  it('keeps the newest search rows when an older search answers last', async () => {
    await rendered();
    await searchFor('b');
    await searchFor('bu');
    await act(async () => {
      requestFor('bu').resolve({ groups: [group('2', 'Builders')], totalCount: 1 });
    });
    await act(async () => {
      requestFor('b').resolve({ groups: [group('1', 'Admins'), group('2', 'Builders')], totalCount: 2 });
    });
    expect(names()).toEqual(['Builders']);
    expect(useGroupsStore.getState().totalCount).toBe(1);
  });

  it('ignores an older request failing after a newer one answered, and leaves loading to the newest', async () => {
    await rendered();
    await searchFor('c');
    await searchFor('cr');
    await act(async () => {
      requestFor('c').reject(new Error('boom'));
    });
    expect(useGroupsStore.getState().error).toBeNull();
    expect(useGroupsStore.getState().isLoading).toBe(true);
    await act(async () => {
      requestFor('cr').resolve({ groups: [group('3', 'Crew')], totalCount: 1 });
    });
    expect(names()).toEqual(['Crew']);
    expect(useGroupsStore.getState().isLoading).toBe(false);
  });
});
