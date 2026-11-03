import { afterEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  listMembers: vi.fn(),
  share: vi.fn(),
  unshare: vi.fn(),
  getUsersByIds: vi.fn(),
}));

vi.mock('../api', () => ({
  ArtifactsApi: {
    listMembers: apiMocks.listMembers,
    share: apiMocks.share,
    unshare: apiMocks.unshare,
  },
}));
vi.mock('@/app/components/share/api', () => ({
  ShareCommonApi: { getUsersByIds: apiMocks.getUsersByIds },
}));
vi.mock('@/app/components/share/utils', () => ({ fetchShareUsersPaginated: vi.fn() }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: { getState: () => ({ profile: { userId: 'owner-id' } }) },
}));

import { createArtifactShareAdapter } from '../share-adapter';
import { parseArtifactsParams, serializeArtifactsParams } from '../url-params';

afterEach(() => vi.clearAllMocks());

describe('artifact share adapter', () => {
  it('lists the owner first with names, and marks who is looking', async () => {
    apiMocks.listMembers.mockResolvedValue([
      { userId: 'reader-id', role: 'READER' },
      { userId: 'owner-id', role: 'OWNER' },
    ]);
    apiMocks.getUsersByIds.mockResolvedValue([
      { id: 'owner-id', name: 'Ada', email: 'ada@example.com', isInOrg: true },
      { id: 'reader-id', name: '', email: 'bob@example.com', isInOrg: true },
    ]);

    const members = await createArtifactShareAdapter('art-1').getSharedMembers();

    expect(apiMocks.listMembers).toHaveBeenCalledWith('art-1');
    expect(members.map((m) => [m.id, m.name, m.role, m.isOwner, m.isCurrentUser])).toEqual([
      ['owner-id', 'Ada', 'OWNER', true, true],
      ['reader-id', 'bob@example.com', 'READER', false, false],
    ]);
  });

  it('shares with users, not teams, and removes one user at a time', async () => {
    const adapter = createArtifactShareAdapter('art-1');
    expect(adapter.supportsTeams).toBe(false);
    expect(adapter.supportsRoles).toBe(false);

    await adapter.share({ userIds: ['u1', 'u2'], teamIds: [], role: 'READER' });
    await adapter.share({ userIds: [], teamIds: ['t1'], role: 'READER' });
    await adapter.removeMember('u1', 'user');

    expect(apiMocks.share).toHaveBeenCalledTimes(1);
    expect(apiMocks.share).toHaveBeenCalledWith('art-1', ['u1', 'u2']);
    expect(apiMocks.unshare).toHaveBeenCalledWith('art-1', ['u1']);
  });
});

describe('artifacts URL state', () => {
  it('keeps the tab in the URL and leaves the default tab out', () => {
    const shared = parseArtifactsParams(new URLSearchParams('tab=shared&view=grid'));
    expect(shared.ownership).toBe('shared');
    expect(serializeArtifactsParams(shared)).toBe('view=grid&tab=shared');

    const all = parseArtifactsParams(new URLSearchParams('tab=everything'));
    expect(all.ownership).toBe('all');
    expect(serializeArtifactsParams(all)).toBe('');
  });

  it('drops a deep-linked artifactId when re-serialized', () => {
    const state = parseArtifactsParams(new URLSearchParams('tab=shared&artifactId=art-1'));
    expect(serializeArtifactsParams(state)).toBe('tab=shared');
  });
});
