import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({ getCollaborators: vi.fn() }));
vi.mock('@/chat/collaboration-api', () => ({ CollaborationApi: { getCollaborators: api.getCollaborators } }));

import { buildMentionables, useMentionables, MENTION_RESULT_LIMIT } from '../use-mentionables';
import { useChatStore } from '@/chat/store';
import { useUserStore } from '@/lib/store/user-store';
import { useParticipantsStore } from '@/chat/mentions/participants-store';
import type { CollaboratorsView } from '@/chat/collaboration-types';

const view: CollaboratorsView = {
  owner: { userId: 'o1', displayName: 'Olive Owner' },
  collaborators: [
    { principalType: 'user', principalId: 'u1', displayName: 'Bob Builder', accessLevel: 'write', state: 'active' },
    { principalType: 'user', principalId: 'me', displayName: 'Myself', accessLevel: 'write', state: 'active' },
    { principalType: 'user', principalId: 'u2', displayName: 'Gone Gary', accessLevel: 'read', state: 'former_member' },
    { principalType: 'team', principalId: 't1', displayName: 'Sales Team', accessLevel: 'read', state: 'active' },
    { principalType: 'team', principalId: 't2', displayName: 'Old Team', accessLevel: 'read', state: 'deleted_team' },
  ],
  collaboratorCount: 5,
  settings: { editorsCanInvite: false, ownerContentShared: false },
};

describe('buildMentionables', () => {
  const base = { assistantLabel: 'Assistant', meUserId: 'me' };
  const labels = (items: ReturnType<typeof buildMentionables>) => items.map((i) => `${i.group}:${i.label}`);

  it('orders assistant, agent, people, teams; skips the caller, former members and deleted teams', () => {
    const items = buildMentionables({ ...base, agent: { id: 'ag1', name: 'HR Agent' }, collaborators: view }, '');
    expect(labels(items)).toEqual([
      'assistant:Assistant',
      'agent:HR Agent',
      'people:Olive Owner',
      'people:Bob Builder',
      'teams:Sales Team',
    ]);
  });

  it('a summary view (non-manager) yields the owner and the feed authors only', () => {
    const items = buildMentionables(
      {
        ...base,
        collaborators: { owner: view.owner, collaboratorCount: 9, myAccess: 'write' },
        authors: [{ userId: 'u9', displayName: 'Cara' }, { userId: 'me', displayName: 'Myself' }, { userId: 'o1', displayName: 'Olive Owner' }, { userId: 'u8', displayName: null }],
      },
      '',
    );
    expect(labels(items)).toEqual(['assistant:Assistant', 'people:Olive Owner', 'people:Cara']);
  });

  it('matches the start of any word, and the reserved names for the assistant', () => {
    const sources = { ...base, collaborators: view };
    expect(labels(buildMentionables(sources, 'bui'))).toEqual(['people:Bob Builder']);
    expect(labels(buildMentionables(sources, 'owner'))).toEqual(['people:Olive Owner']);
    expect(labels(buildMentionables(sources, 'pipe'))).toEqual(['assistant:Assistant']);
    expect(labels(buildMentionables(sources, 'zzz'))).toEqual([]);
  });

  it('keeps an agent row apart from the assistant alias when both are called "Assistant" (MN-17)', () => {
    const items = buildMentionables({ ...base, agent: { id: 'ag1', name: 'Assistant' } }, 'assistant');
    expect(items.map((i) => `${i.ref.type}:${i.ref.id}`)).toEqual(['assistant:self', 'agent:ag1']);
  });

  it('caps the list', () => {
    const many: CollaboratorsView = {
      ...view,
      collaborators: Array.from({ length: 60 }, (_, i) => ({
        principalType: 'user' as const,
        principalId: `p${i}`,
        displayName: `Person ${i}`,
        accessLevel: 'read' as const,
        state: 'active' as const,
      })),
    };
    expect(buildMentionables({ ...base, collaborators: many }, '')).toHaveLength(MENTION_RESULT_LIMIT);
  });
});

describe('useMentionables', () => {
  const initial = useChatStore.getState();
  beforeEach(() => {
    useChatStore.setState(initial, true);
    const id = useChatStore.getState().createSlot(null);
    useChatStore.getState().setActiveSlot(id);
    useChatStore.getState().updateSlot(id, { convId: 'c1' });
    useUserStore.setState({ profile: { userId: 'me' } as never });
    api.getCollaborators.mockReset();
    api.getCollaborators.mockResolvedValue(view);
    useParticipantsStore.getState().reset();
  });
  afterEach(() => vi.useRealTimers());

  it('does not call the API until enabled, then fetches once for the chat', async () => {
    const { result, rerender } = renderHook((p: { enabled: boolean; query: string }) => useMentionables({ ...p, assistantLabel: 'Assistant' }), {
      initialProps: { enabled: false, query: '' },
    });
    expect(api.getCollaborators).not.toHaveBeenCalled();
    expect(result.current.items).toEqual([]);
    rerender({ enabled: true, query: '' });
    await waitFor(() => expect(result.current.items.length).toBe(4));
    rerender({ enabled: true, query: 'b' });
    expect(api.getCollaborators).toHaveBeenCalledTimes(1);
    expect(api.getCollaborators.mock.calls[0][0]).toEqual({ kind: 'chat', id: 'c1' });
  });

  it('debounces the query by 150 ms', async () => {
    const { result, rerender } = renderHook((p: { query: string }) => useMentionables({ enabled: true, query: p.query, assistantLabel: 'Assistant' }), {
      initialProps: { query: '' },
    });
    await waitFor(() => expect(result.current.items.length).toBe(4));
    vi.useFakeTimers();
    rerender({ query: 'bob' });
    act(() => {
      vi.advanceTimersByTime(149);
    });
    expect(result.current.items.length).toBe(4);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.items.map((i) => i.label)).toEqual(['Bob Builder']);
  });

  it('shares one fetch with the send path: the popover reuses the participants the chat already loaded', async () => {
    await act(async () => {
      await useParticipantsStore.getState().load({ kind: 'chat', id: 'c1' });
    });
    expect(api.getCollaborators).toHaveBeenCalledTimes(1);
    const { result } = renderHook(() => useMentionables({ query: '', enabled: true, assistantLabel: 'Assistant' }));
    await waitFor(() => expect(result.current.items.length).toBe(4));
    expect(result.current.loading).toBe(false);
    expect(api.getCollaborators).toHaveBeenCalledTimes(1);
  });

  it('uses the agent endpoint and lists the chat’s own agent in an agent chat', async () => {
    const id = useChatStore.getState().activeSlotId!;
    useChatStore.getState().updateSlot(id, { threadAgentId: 'agent-1' });
    useChatStore.setState({ agentContextDisplayName: 'HR Agent' });
    const { result } = renderHook(() => useMentionables({ query: '', enabled: true, assistantLabel: 'Assistant' }));
    await waitFor(() => expect(result.current.items.some((i) => i.group === 'people')).toBe(true));
    expect(api.getCollaborators.mock.calls[0][0]).toEqual({ kind: 'agent', agentKey: 'agent-1', id: 'c1' });
    expect(result.current.items.find((i) => i.group === 'agent')?.ref).toEqual({ type: 'agent', id: 'agent-1' });
  });
});

describe('buildMentionables own agents (PH-11.4)', () => {
  const ownAgents = [{ id: 'agent-9', label: 'Offer drafter', handle: 'offer-drafter' }];

  it('offers the caller’s agents after the assistant, matched by name word or handle prefix', () => {
    const all = buildMentionables({ assistantLabel: 'PipesHub', ownAgents }, '');
    expect(all.map((m) => `${m.group}:${m.label}`)).toEqual(['assistant:PipesHub', 'agent:Offer drafter']);
    expect(buildMentionables({ assistantLabel: 'PipesHub', ownAgents }, 'draf').map((m) => m.label)).toEqual(['Offer drafter']);
    expect(buildMentionables({ assistantLabel: 'PipesHub', ownAgents }, '@offer-d').map((m) => m.label)).toEqual(['Offer drafter']);
    expect(buildMentionables({ assistantLabel: 'PipesHub', ownAgents }, 'zzz')).toEqual([]);
    expect(all[1].handle).toBe('offer-drafter');
  });

  it('is unchanged when the server offers no agents', () => {
    expect(buildMentionables({ assistantLabel: 'PipesHub' }, '').map((m) => m.group)).toEqual(['assistant']);
    expect(buildMentionables({ assistantLabel: 'PipesHub', ownAgents: [] }, '').map((m) => m.group)).toEqual(['assistant']);
  });
});

