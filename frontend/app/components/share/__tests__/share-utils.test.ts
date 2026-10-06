import { describe, it, expect, vi } from 'vitest';

const fetchMergedUsers = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { fetchMergedUsers } }));

import { fetchShareUsersPaginated } from '../utils';

const u = (userId: string, extra = {}) => ({ id: userId, userId, name: userId, email: `${userId}@x.io`, hasLoggedIn: true, isActive: true, ...extra });

describe('fetchShareUsersPaginated', () => {
  it('returns everyone by default and drops what exclude rejects', async () => {
    fetchMergedUsers.mockResolvedValue({ users: [u('a'), u('b', { isDisabled: true })], totalCount: 2 });
    expect((await fetchShareUsersPaginated({ page: 1, limit: 10 })).users.map((x) => x.id)).toEqual(['a', 'b']);
    const filtered = await fetchShareUsersPaginated({ page: 1, limit: 10 }, { exclude: (x) => x.isDisabled === true });
    expect(filtered.users.map((x) => x.id)).toEqual(['a']);
  });
});
