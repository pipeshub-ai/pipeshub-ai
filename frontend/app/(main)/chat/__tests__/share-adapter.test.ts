import { describe, it, expect, vi, beforeEach } from 'vitest';
import i18next from 'i18next';

import en from '@/lib/i18n/locales/en-US.json';

const apiClient = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ apiClient }));
vi.mock('@/config', () => ({ useAuthStore: { getState: () => ({ user: { id: 'me' } }) } }));
vi.mock('@/lib/store/user-store', () => ({ useUserStore: { getState: () => ({ profile: { userId: 'me' } }) } }));
vi.mock('@/app/components/share/utils', () => ({ fetchShareUsersPaginated: vi.fn() }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds: vi.fn().mockResolvedValue([]) } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: { fetchAgentConversation: vi.fn() } }));

import { createChatShareAdapter } from '../share-adapter';
import { buildShareSubmission } from '@/app/components/share/types';

beforeEach(async () => {
  vi.clearAllMocks();
  await i18next.init({ lng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
});

describe('collaborative chat share adapter (FE-09)', () => {
  it('PUTs a user and a team added as Can continue, with the note', async () => {
    apiClient.put.mockResolvedValue({ data: {} });
    const adapter = createChatShareAdapter('c1', { collaborative: true });
    const submission = buildShareSubmission(
      [
        { type: 'user', id: 'u2', name: 'Bob' },
        { type: 'team', id: 't1', name: 'Eng', role: 'WRITER' },
      ],
      'WRITER',
    );
    await adapter.share({ ...submission, note: 'take over' });

    const [url, body] = apiClient.put.mock.calls[0];
    expect(url).toBe('/api/v1/conversations/c1/collaborators');
    expect(body).toEqual({
      collaborators: [
        { principalType: 'user', principalId: 'u2', accessLevel: 'write' },
        { principalType: 'team', principalId: 't1', accessLevel: 'write' },
      ],
      note: 'take over',
    });
  });

  it('uses the agent route for an agent ref', async () => {
    apiClient.put.mockResolvedValue({ data: {} });
    const adapter = createChatShareAdapter('c1', { agentId: 'a1', collaborative: true });
    await adapter.share(buildShareSubmission([{ type: 'user', id: 'u2', name: 'Bob' }], 'READER'));
    expect(apiClient.put.mock.calls[0][0]).toBe('/api/v1/agents/a1/conversations/c1/collaborators');
  });

  it('sends confirmOrgWide only with an org-wide team', async () => {
    apiClient.put.mockResolvedValue({ data: {} });
    const adapter = createChatShareAdapter('c1', { collaborative: true });
    const withTeam = buildShareSubmission([{ type: 'team', id: 'all_o1', name: 'Everyone' }], 'READER');
    await adapter.share({ ...withTeam, confirmOrgWide: true });
    expect(apiClient.put.mock.calls[0][1].confirmOrgWide).toBe(true);

    const userOnly = buildShareSubmission([{ type: 'user', id: 'u2', name: 'Bob' }], 'READER');
    await adapter.share({ ...userOnly, confirmOrgWide: true });
    expect(apiClient.put.mock.calls[1][1]).not.toHaveProperty('confirmOrgWide');
  });

  it('offers Can continue and Can view, no OWNER, and describes the disclosure', () => {
    const adapter = createChatShareAdapter('c1', { collaborative: true });
    expect(adapter.roleOptions?.map((o) => [o.role, o.label])).toEqual([
      ['WRITER', 'Can continue'],
      ['READER', 'Can view'],
    ]);
    expect(adapter.supportsRoles && adapter.supportsTeams).toBe(true);
    expect(adapter.notice).toBe(en.chat.collab.share.notice);
    expect(adapter.sidebarTitle).toBe(en.chat.collab.share.title);
    expect(adapter.noteField).toEqual({ maxLength: 500 });
  });

  it('asks for confirmation for an org-wide team and for a team over 50 members only', () => {
    const adapter = createChatShareAdapter('c1', { collaborative: true });
    const sub = buildShareSubmission([], 'READER');
    const team = (id: string, memberCount: number) => ({ type: 'team' as const, id, name: id, memberCount });
    expect(adapter.requiresConfirm?.(sub, [team('all_o1', 3)])).toMatchObject({ orgWide: true });
    expect(adapter.requiresConfirm?.(sub, [team('t1', 51)])).toMatchObject({ title: en.chat.collab.share.largeTeamTitle });
    expect(adapter.requiresConfirm?.(sub, [team('t1', 51)])?.orgWide).toBeUndefined();
    expect(adapter.requiresConfirm?.(sub, [team('t1', 50)])).toBeNull();
    expect(adapter.requiresConfirm?.(sub, [{ type: 'user', id: 'u', name: 'u' }])).toBeNull();
  });

  it('derives the mode from the response: owner manages, an editor invites, a reader sees a summary', async () => {
    const adapter = createChatShareAdapter('c1', { collaborative: true });
    const view = (ownerId: string) => ({
      owner: { userId: ownerId, displayName: 'O' },
      collaborators: [],
      collaboratorCount: 0,
      settings: { editorsCanInvite: true, ownerContentShared: false },
    });
    apiClient.get.mockResolvedValueOnce({ data: view('me') });
    await adapter.getSharedMembers?.();
    expect(adapter.getMode?.()).toBe('manage');
    expect((await adapter.settings?.())?.map((s) => [s.id, s.value])).toEqual([
      ['editorsCanInvite', true],
      ['ownerContentShared', false],
    ]);

    apiClient.get.mockResolvedValueOnce({ data: view('someone-else') });
    await adapter.getSharedMembers();
    expect(adapter.getMode?.()).toBe('invite');

    apiClient.get.mockResolvedValueOnce({ data: { owner: { userId: 'x', displayName: 'Xi' }, collaboratorCount: 4, myAccess: 'read' } });
    expect(await adapter.getSharedMembers()).toEqual([]);
    expect(adapter.getMode?.()).toBe('summary');
    expect(adapter.getAccessSummary?.()).toEqual({ count: 4, ownerName: 'Xi', myAccessLabel: 'Can view' });
  });

  it('formats coded errors, including retryAfter and max', () => {
    const adapter = createChatShareAdapter('c1', { collaborative: true });
    expect(adapter.formatError?.({ code: 'RATE_LIMITED', details: { retryAfter: 9 } })).toBe(
      'Too many changes. Try again in 9 seconds.',
    );
    expect(adapter.formatError?.({ code: 'COLLABORATOR_LIMIT', details: { max: 200 } })).toContain('200');
    expect(adapter.formatError?.({ code: 'INVALID_PRINCIPAL' })).toBe(en.chat.collab.errors.INVALID_PRINCIPAL);
  });
});

describe('flag off: the legacy adapter is unchanged', () => {
  it('without the collaborative option keeps no roles, no teams and the share/unshare routes', async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    const adapter = createChatShareAdapter('c1');
    expect(adapter.sidebarTitle).toBe('Share Chat');
    expect(adapter.supportsRoles).toBe(false);
    expect(adapter.supportsTeams).toBe(false);
    for (const key of ['roleOptions', 'settings', 'updateSetting', 'notice', 'noteField', 'transferOwnership', 'requiresConfirm', 'getMode', 'formatError'] as const) {
      expect(adapter[key]).toBeUndefined();
    }

    await adapter.share(buildShareSubmission([{ type: 'user', id: 'u2', name: 'Bob' }], 'READER'));
    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/conversations/c1/share', { userIds: ['u2'], accessLevel: 'read' });
    await adapter.removeMember('u2', 'user');
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/v1/conversations/c1/unshare', { userIds: ['u2'] });
    expect(apiClient.put).not.toHaveBeenCalled();
  });

  it('keeps the agent share route', async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await createChatShareAdapter('c1', { agentId: 'a1' }).share(buildShareSubmission([{ type: 'user', id: 'u2', name: 'Bob' }], 'READER'));
    expect(apiClient.post.mock.calls[0][0]).toBe('/api/v1/agents/a1/conversations/c1/share');
  });
});
