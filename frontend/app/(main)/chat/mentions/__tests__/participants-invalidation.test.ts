/**
 * The @ list must follow who is in the chat without a reload: every successful collaborator
 * mutation invalidates the cache, a feed that reports a new aclVersion does too, and a failed
 * call or an unchanged version leaves it alone.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

const http = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient: http }));
vi.mock('@/lib/api/axios-instance', () => ({ apiClient: http }));

import { CollaborationApi } from '../../collaboration-api';
import { useParticipantsStore } from '../participants-store';
import { useMentionables } from '../../components/composer/use-mentionables';
import { applyFeedPage } from '../../utils/apply-feed-page';
import { useChatStore } from '../../store';
import { useUserStore } from '@/lib/store/user-store';
import type { CollaboratorsView, ConversationRef, FeedPage } from '../../collaboration-types';

const ref: ConversationRef = { kind: 'chat', id: 'c1' };
const view = (names: string[]): CollaboratorsView => ({
  owner: { userId: 'o1', displayName: 'Olive Owner' },
  collaborators: names.map((n, i) => ({ principalType: 'user' as const, principalId: `u${i}`, displayName: n, accessLevel: 'write' as const, state: 'active' as const })),
  collaboratorCount: names.length,
  settings: { editorsCanInvite: false, ownerContentShared: false },
});
const initialChat = useChatStore.getState();

function route(current: () => CollaboratorsView) {
  http.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/collaborators')) return { data: current() };
    if (url.endsWith('/mentionables')) return { data: { items: [] } };
    return { data: {} };
  });
}

beforeEach(() => {
  Object.values(http).forEach((m) => m.mockReset());
  useParticipantsStore.getState().reset();
  useChatStore.setState(initialChat, true);
  const id = useChatStore.getState().createSlot(null);
  useChatStore.getState().setActiveSlot(id);
  useChatStore.getState().updateSlot(id, { convId: 'c1' });
  useUserStore.setState({ profile: { userId: 'me' } as never });
});

describe('the picker after a share', () => {
  it('shows a person right after the share call succeeds, with no reload', async () => {
    let people = ['Bob Builder'];
    route(() => view(people));
    const { result } = renderHook(() => useMentionables({ enabled: true, query: '', assistantLabel: 'Assistant' }));
    await waitFor(() => expect(result.current.items.map((i) => i.label)).toContain('Bob Builder'));
    expect(result.current.items.map((i) => i.label)).not.toContain('Cara Newly');

    people = ['Bob Builder', 'Cara Newly'];
    http.put.mockResolvedValue({ data: view(people) });
    await act(async () => {
      await CollaborationApi.putCollaborators(ref, { collaborators: [{ principalType: 'user', principalId: 'u1', accessLevel: 'write' }] });
    });
    await waitFor(() => expect(result.current.items.map((i) => i.label)).toContain('Cara Newly'));
  });

  it('drops a removed person the same way', async () => {
    let people = ['Bob Builder', 'Cara Newly'];
    route(() => view(people));
    const { result } = renderHook(() => useMentionables({ enabled: true, query: '', assistantLabel: 'Assistant' }));
    await waitFor(() => expect(result.current.items.map((i) => i.label)).toContain('Cara Newly'));
    people = ['Bob Builder'];
    http.delete.mockResolvedValue({ data: view(people) });
    await act(async () => {
      await CollaborationApi.removeCollaborator(ref, 'u1');
    });
    await waitFor(() => expect(result.current.items.map((i) => i.label)).not.toContain('Cara Newly'));
  });

  it('a load that was in flight when the list changed does not put the old list back', async () => {
    let release: (v: unknown) => void = () => {};
    http.get.mockImplementation((url: string) =>
      url.endsWith('/collaborators')
        ? new Promise((resolve) => { release = resolve; })
        : Promise.resolve({ data: { items: [] } }),
    );
    void useParticipantsStore.getState().load(ref);
    useParticipantsStore.getState().invalidate('c1');
    release({ data: view(['Old Person']) });
    await new Promise((r) => setTimeout(r, 0));
    expect(useParticipantsStore.getState().byConv.c1).toBeUndefined();
  });
});

describe('every collaborator mutation invalidates', () => {
  const invalidate = () => vi.spyOn(useParticipantsStore.getState(), 'invalidate');
  const cases: Array<[string, () => Promise<unknown>]> = [
    ['putCollaborators', () => { http.put.mockResolvedValue({ data: view([]) }); return CollaborationApi.putCollaborators(ref, { collaborators: [] }); }],
    ['removeCollaborator', () => { http.delete.mockResolvedValue({ data: view([]) }); return CollaborationApi.removeCollaborator(ref, 'u1'); }],
    ['patchSettings (editors can invite, share files)', () => { http.patch.mockResolvedValue({ data: view([]) }); return CollaborationApi.patchSettings(ref, { editorsCanInvite: true }); }],
    ['transferOwnership', () => { http.post.mockResolvedValue({ data: view([]) }); return CollaborationApi.transferOwnership(ref, 'u2'); }],
    ['leave', () => { http.post.mockResolvedValue({ data: {} }); return CollaborationApi.leave(ref); }],
  ];

  it.each(cases)('%s', async (_name, call) => {
    const spy = invalidate();
    await call();
    expect(spy).toHaveBeenCalledExactlyOnceWith('c1');
    spy.mockRestore();
  });

  it('a failed call leaves the cache alone', async () => {
    const spy = invalidate();
    http.put.mockRejectedValue(new Error('boom'));
    await expect(CollaborationApi.putCollaborators(ref, { collaborators: [] })).rejects.toThrow('boom');
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('aclVersion on the feed', () => {
  const page = (aclVersion?: string | number): FeedPage => ({
    messages: [], rev: 1, nextSeq: 0, hasMore: false, activeRun: null, lastActivityAt: 0,
    ...(aclVersion !== undefined ? { aclVersion } : {}),
  });
  const slotId = () => useChatStore.getState().activeSlotId!;

  it('a changed version refetches once; the first sight and an unchanged version do not', async () => {
    let n = 0;
    http.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/collaborators')) { n += 1; return { data: view([]) }; }
      return { data: { items: [] } };
    });
    const { result } = renderHook(() => useMentionables({ enabled: true, query: '', assistantLabel: 'Assistant' }));
    await waitFor(() => expect(n).toBe(1));
    expect(result.current.loading).toBe(false);

    applyFeedPage(slotId(), page('v1'));
    applyFeedPage(slotId(), page('v1'));
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(n).toBe(1);

    applyFeedPage(slotId(), page('v2'));
    await waitFor(() => expect(n).toBe(2));
    applyFeedPage(slotId(), page('v2'));
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(n).toBe(2);
  });

  it('reads the X-Acl-Version header, so a 304 (nothing new in the feed) still reveals a sharing change', async () => {
    const feed = (status: number, version: number) => ({ status, data: status === 304 ? '' : page(version), headers: { 'x-acl-version': String(version) } });
    http.get.mockResolvedValueOnce(feed(304, 4));
    await CollaborationApi.fetchFeed(ref, { afterSeq: 1, rev: 1 });
    expect(useParticipantsStore.getState().aclSeen.c1).toBe(4);
    expect(useParticipantsStore.getState().epoch).toEqual({});
    http.get.mockResolvedValueOnce(feed(304, 5));
    await CollaborationApi.fetchFeed(ref, { afterSeq: 1, rev: 1 });
    expect(useParticipantsStore.getState().epoch.c1).toBe(1);
    http.get.mockResolvedValueOnce({ status: 304, data: '', headers: {} });
    await CollaborationApi.fetchFeed(ref, { afterSeq: 1, rev: 1 });
    expect(useParticipantsStore.getState().epoch.c1).toBe(1);
  });

  it('is tolerant of a server that sends no version', async () => {
    route(() => view([]));
    useParticipantsStore.getState().noteAclVersion('c1', undefined);
    applyFeedPage(slotId(), page());
    expect(useParticipantsStore.getState().aclSeen).toEqual({});
    expect(useParticipantsStore.getState().epoch).toEqual({});
  });
});
