import { beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({
  post: vi.fn(),
  getUsersByIds: vi.fn(),
  fetchConversation: vi.fn(),
  fetchAgentConversation: vi.fn(),
  profile: { userId: 'me', fullName: 'Asha Rao', email: 'asha@acme.test', avatarUrl: null } as Record<string, unknown> | null,
}));

vi.mock('@/lib/api', () => ({ apiClient: { post: h.post } }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds: h.getUsersByIds } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: { fetchAgentConversation: h.fetchAgentConversation } }));
vi.mock('../api', () => ({ ChatApi: { fetchConversation: h.fetchConversation } }));
vi.mock('../project-api', () => ({ ProjectApi: {} }));
vi.mock('@/config', async (importOriginal) => ({
  chatExtension: (await importOriginal<typeof import('@/config')>()).chatExtension,
  useAuthStore: { getState: () => ({ user: null }) },
}));
vi.mock('@/lib/store/user-store', () => ({ useUserStore: { getState: () => ({ profile: h.profile }) } }));

import { useChatStore } from '../store';
import { createChatShareAdapter } from '../share-adapter';
import type { SharedWithEntry } from '../types';

const BEN: SharedWithEntry = { userId: 'u-ben', accessLevel: 'read', _id: 's1' };
const CARA: SharedWithEntry = { userId: 'u-cara', accessLevel: 'write', _id: 's2' };

/** A slot for `convId` as `loadHistory` leaves it; returns its id. */
function openSlot(convId: string, sharedWith: SharedWithEntry[] | null, { active = true } = {}): string {
  const store = useChatStore.getState();
  const slotId = store.createSlot(convId);
  store.updateSlot(slotId, { isInitialized: true, isOwner: true, sharedWith });
  if (active) store.setActiveSlot(slotId);
  return slotId;
}

const sharedWithOf = (slotId: string) => useChatStore.getState().slots[slotId]?.sharedWith;

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.getState().reset();
  h.profile = { userId: 'me', fullName: 'Asha Rao', email: 'asha@acme.test', avatarUrl: null };
  h.getUsersByIds.mockResolvedValue([
    { userId: 'u-ben', name: 'Ben Cho', email: 'ben@acme.test' },
    { userId: 'u-cara', name: 'Cara Diaz', email: 'cara@acme.test' },
  ]);
});

describe('chat share adapter', () => {
  it('lists the members from the loaded conversation, without fetching it again', async () => {
    openSlot('conv-1', [BEN, CARA]);

    const members = await createChatShareAdapter('conv-1').getSharedMembers();

    expect(h.fetchConversation).not.toHaveBeenCalled();
    expect(members.map((m) => [m.name, m.role])).toEqual([
      ['Asha Rao', 'OWNER'],
      ['Ben Cho', 'READER'],
      ['Cara Diaz', 'WRITER'],
    ]);
    expect(members[0]).toMatchObject({ id: 'me', isOwner: true, isCurrentUser: true });
  });

  it('looks up only the people it is shared with, never the owner', async () => {
    openSlot('conv-1', [BEN, { userId: 'me', accessLevel: 'read', _id: 's0' }]);

    const members = await createChatShareAdapter('conv-1').getSharedMembers();

    expect(h.getUsersByIds).toHaveBeenCalledWith(['u-ben']);
    expect(members).toHaveLength(2);
  });

  it('makes no lookup for a chat shared with nobody', async () => {
    openSlot('conv-1', []);

    const members = await createChatShareAdapter('conv-1').getSharedMembers();

    expect(h.getUsersByIds).not.toHaveBeenCalled();
    expect(members.map((m) => m.name)).toEqual(['Asha Rao']);
  });

  it("names the owner 'Owner' while the profile has no name yet", async () => {
    h.profile = { userId: 'me', fullName: null, email: null, avatarUrl: null };
    openSlot('conv-1', []);

    const [owner] = await createChatShareAdapter('conv-1').getSharedMembers();

    expect(owner.name).toBe('Owner');
  });

  it('a new chat knows it is shared with nobody, without a request', async () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot(null);
    store.resolveSlotConvId(slotId, 'conv-new');

    await createChatShareAdapter('conv-new').getSharedMembers();

    expect(sharedWithOf(slotId)).toEqual([]);
    expect(h.fetchConversation).not.toHaveBeenCalled();
  });

  it('fetches the smallest page once when the sharing is not known, and keeps it', async () => {
    const slotId = openSlot('conv-1', null);
    h.fetchConversation.mockResolvedValue({ conversation: { sharedWith: [BEN] } });
    const adapter = createChatShareAdapter('conv-1');

    await adapter.getSharedMembers();
    await adapter.getSharedMembers();

    expect(h.fetchConversation).toHaveBeenCalledTimes(1);
    expect(h.fetchConversation).toHaveBeenCalledWith('conv-1', 1, 1);
    expect(sharedWithOf(slotId)).toEqual([BEN]);
  });

  it('fetches an agent conversation from the agent route when needed', async () => {
    openSlot('conv-1', null);
    h.fetchAgentConversation.mockResolvedValue({ conversation: { sharedWith: [] } });

    await createChatShareAdapter('conv-1', { agentId: 'agent-7' }).getSharedMembers();

    expect(h.fetchAgentConversation).toHaveBeenCalledWith('agent-7', 'conv-1', { page: 1, limit: 1 });
    expect(h.fetchConversation).not.toHaveBeenCalled();
  });

  it("stores the server's answer after a share", async () => {
    const slotId = openSlot('conv-1', []);
    h.post.mockResolvedValue({ data: { sharedWith: [BEN] } });

    await createChatShareAdapter('conv-1').share({ userIds: ['u-ben'], teamIds: [], role: 'READER' });

    expect(h.post).toHaveBeenCalledWith('/api/v1/conversations/conv-1/share', { userIds: ['u-ben'], accessLevel: 'read' });
    expect(sharedWithOf(slotId)).toEqual([BEN]);
  });

  it("stores the server's answer after removing someone", async () => {
    const slotId = openSlot('conv-1', [BEN, CARA]);
    h.post.mockResolvedValue({ data: { sharedWith: [CARA] } });

    await createChatShareAdapter('conv-1', { agentId: 'agent-7' }).removeMember('u-ben', 'user');

    expect(h.post).toHaveBeenCalledWith('/api/v1/agents/agent-7/conversations/conv-1/unshare', { userIds: ['u-ben'] });
    expect(sharedWithOf(slotId)).toEqual([CARA]);
  });

  it('forgets the sharing when a share answer leaves it out, so the next read asks', async () => {
    const slotId = openSlot('conv-1', []);
    h.post.mockResolvedValue({ data: {} });
    h.fetchConversation.mockResolvedValue({ conversation: { sharedWith: [BEN] } });
    const adapter = createChatShareAdapter('conv-1');

    await adapter.share({ userIds: ['u-ben'], teamIds: [], role: 'READER' });
    expect(sharedWithOf(slotId)).toBeNull();

    await adapter.getSharedMembers();
    expect(h.fetchConversation).toHaveBeenCalledTimes(1);
  });

  it('leaves the slot alone when a share fails', async () => {
    const slotId = openSlot('conv-1', [BEN]);
    h.post.mockRejectedValue(new Error('500'));

    await expect(
      createChatShareAdapter('conv-1').share({ userIds: ['u-cara'], teamIds: [], role: 'READER' }),
    ).rejects.toThrow('500');

    expect(sharedWithOf(slotId)).toEqual([BEN]);
  });

  it('updates every slot of the conversation, and reads the open one first', async () => {
    const background = openSlot('conv-1', [CARA], { active: false });
    const open = openSlot('conv-1', [BEN]);
    h.post.mockResolvedValue({ data: { sharedWith: [BEN, CARA] } });
    const adapter = createChatShareAdapter('conv-1');

    const before = await adapter.getSharedMembers();
    expect(before.map((m) => m.name)).toEqual(['Asha Rao', 'Ben Cho']);

    await adapter.share({ userIds: ['u-cara'], teamIds: [], role: 'READER' });

    expect(sharedWithOf(open)).toEqual([BEN, CARA]);
    expect(sharedWithOf(background)).toEqual([BEN, CARA]);
  });
});
