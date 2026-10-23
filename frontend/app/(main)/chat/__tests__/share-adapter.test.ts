import { beforeEach, describe, expect, it, vi } from 'vitest';

const post = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {} }));
vi.mock('@/lib/api', () => ({ apiClient: { post, get: vi.fn() } }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: {} }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));
vi.mock('@/app/components/share/utils', () => ({ fetchShareUsersPaginated: vi.fn() }));
vi.mock('@/app/components/share/api', () => ({ ShareCommonApi: {} }));
vi.mock('./project-api', () => ({ ProjectApi: {} }));

import { createChatShareAdapter } from '../share-adapter';

beforeEach(() => post.mockClear());

describe('chat share adapter levels', () => {
  const adapter = createChatShareAdapter('c1');

  it('offers Can edit and Can view only (the owner stays the owner)', () => {
    expect(adapter.supportsRoles).toBe(true);
    expect(adapter.roleOptions?.roles).toEqual(['WRITER', 'READER']);
  });

  it('offers Can view only for an agent conversation, which is shared read-only', () => {
    expect(createChatShareAdapter('c1', { agentId: 'a1' }).roleOptions?.roles).toEqual(['READER']);
  });

  it('shares with the chosen level', async () => {
    await adapter.share({ userIds: ['u1'], role: 'WRITER' } as never);
    await adapter.share({ userIds: ['u2'], role: 'READER' } as never);
    expect(post).toHaveBeenNthCalledWith(1, '/api/v1/conversations/c1/share', { userIds: ['u1'], accessLevel: 'write' });
    expect(post).toHaveBeenNthCalledWith(2, '/api/v1/conversations/c1/share', { userIds: ['u2'], accessLevel: 'read' });
  });

  it('changes an existing member’s level through the same route', async () => {
    await adapter.updateRole?.('u1', 'user', 'READER');
    expect(post).toHaveBeenCalledWith('/api/v1/conversations/c1/share', { userIds: ['u1'], accessLevel: 'read' });
  });
});
