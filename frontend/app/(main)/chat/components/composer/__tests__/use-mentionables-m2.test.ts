/**
 * M2: agents in every chat, organization-wide people search with multi-word names, the Add people row,
 * and the new-chat scope (no conversation yet, draft collaborators counted as in the chat).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act, cleanup } from '@testing-library/react';

const collab = vi.hoisted(() => ({ getCollaborators: vi.fn() }));
vi.mock('@/chat/collaboration-api', () => ({ CollaborationApi: { getCollaborators: collab.getCollaborators } }));
const mentionsApi = vi.hoisted(() => ({ listAgents: vi.fn(), search: vi.fn() }));
vi.mock('@/chat/mentions/api', () => ({ MentionsApi: mentionsApi, scopeKey: () => 'k' }));

import { buildMentionables, phraseMatches, useMentionables } from '../use-mentionables';
import { useChatStore } from '@/chat/store';
import { useUserStore } from '@/lib/store/user-store';
import { useParticipantsStore } from '@/chat/mentions/participants-store';
import { useDraftShareStore } from '@/chat/draft-share-store';
import type { CollaboratorsView } from '@/chat/collaboration-types';

const view: CollaboratorsView = {
  owner: { userId: 'o1', displayName: 'Olive Owner' },
  collaborators: [{ principalType: 'user', principalId: 'u1', displayName: 'Bob Builder', accessLevel: 'write', state: 'active' }],
  collaboratorCount: 1,
  settings: { editorsCanInvite: false, ownerContentShared: false },
};
const base = { assistantLabel: 'Assistant', meUserId: 'me' };

describe('phraseMatches', () => {
  it.each([
    ['John Michael Smith', 'jo sm', true],
    ['John Michael Smith', 'jo mi sm', true],
    ['John Michael Smith', 'smith john', false],
    ['John Michael Smith', 'john ', true],
    ['John Smith', 'smith ', false],
    ['John Smith', 'jo  sm', true],
    ['John Smith', 'x', false],
  ])('%s / "%s" -> %s', (label, query, expected) => {
    expect(phraseMatches(label, query)).toBe(expected);
  });
});

describe('buildMentionables (M2)', () => {
  it('lists agents under their own group with the handle, in a default chat', () => {
    const items = buildMentionables({ ...base, ownAgents: [{ id: 'a1', label: 'Joke Buddy', handle: 'joke-buddy' }] }, 'joke');
    expect(items).toEqual([expect.objectContaining({ group: 'agent', label: 'Joke Buddy', handle: 'joke-buddy', ref: { type: 'agent', id: 'a1' } })]);
  });

  it('matches an agent by the start of its handle with or without @', () => {
    const own = [{ id: 'a1', label: 'Joke Buddy', handle: 'jb-bot' }];
    expect(buildMentionables({ ...base, ownAgents: own }, '@jb').map((i) => i.label)).toEqual(['Joke Buddy']);
    expect(buildMentionables({ ...base, ownAgents: own }, 'zz')).toEqual([]);
  });

  it('once an agent is in the composer every other agent is disabled, and that one stays pickable', () => {
    const own = [{ id: 'a1', label: 'Alpha' }, { id: 'a2', label: 'Beta' }];
    const items = buildMentionables({ ...base, ownAgents: own, selectedAgentIds: ['a1'] }, '');
    expect(items.filter((i) => i.group === 'agent').map((i) => [i.label, i.disabled === true])).toEqual([['Alpha', false], ['Beta', true]]);
    expect(buildMentionables({ ...base, ownAgents: own }, '').some((i) => i.disabled)).toBe(false);
  });

  it('the chat’s own agent and the same agent from the server are one row that keeps the handle', () => {
    const items = buildMentionables(
      { ...base, agent: { id: 'a1', name: 'HR Agent' }, ownAgents: [{ id: 'a1', label: 'HR Agent', handle: 'hr' }] },
      '',
    );
    expect(items.filter((i) => i.group === 'agent')).toEqual([expect.objectContaining({ handle: 'hr' })]);
  });

  it('lists people from the organization after those in the chat, with email and inChat', () => {
    const items = buildMentionables(
      {
        ...base,
        collaborators: view,
        remotePeople: [
          { id: 'u1', label: 'Bob Builder', email: 'bob@acme.test', inChat: true },
          { id: 'u9', label: 'Bobby Tables', email: 'bobby@acme.test', inChat: false },
          { id: 'me', label: 'Myself', inChat: false },
        ],
      },
      'bob',
    );
    expect(items.map((i) => [i.group, i.label, i.email, i.inChat])).toEqual([
      ['people', 'Bob Builder', 'bob@acme.test', undefined],
      ['others', 'Bobby Tables', 'bobby@acme.test', false],
    ]);
  });

  it('a person the server matched only by email is still listed', () => {
    const items = buildMentionables({ ...base, remotePeople: [{ id: 'u5', label: 'Zed Zebra', email: 'bob@acme.test', inChat: false }] }, 'bob');
    expect(items.map((i) => i.label)).toEqual(['Zed Zebra']);
  });
});

describe('useMentionables (M2)', () => {
  const initial = useChatStore.getState();
  beforeEach(() => {
    useChatStore.setState(initial, true);
    useUserStore.setState({ profile: { userId: 'me', fullName: 'Me Myself' } as never });
    useParticipantsStore.getState().reset();
    useDraftShareStore.getState().clear();
    collab.getCollaborators.mockReset();
    collab.getCollaborators.mockResolvedValue(view);
    mentionsApi.listAgents.mockReset();
    mentionsApi.listAgents.mockResolvedValue([]);
    mentionsApi.search.mockReset();
    mentionsApi.search.mockResolvedValue({ agents: [], people: [] });
    window.history.replaceState(null, '', '/chat/');
  });
  afterEach(() => vi.useRealTimers());

  const openChat = () => {
    const id = useChatStore.getState().createSlot(null);
    useChatStore.getState().setActiveSlot(id);
    useChatStore.getState().updateSlot(id, { convId: 'c1' });
  };

  it('sends a multi-word query to the server whole, once, after the debounce', async () => {
    openChat();
    const { rerender } = renderHook((p: { query: string }) => useMentionables({ enabled: true, query: p.query, assistantLabel: 'Assistant' }), {
      initialProps: { query: '' },
    });
    await waitFor(() => expect(collab.getCollaborators).toHaveBeenCalled());
    rerender({ query: 'jo' });
    rerender({ query: 'jo s' });
    rerender({ query: 'jo sm' });
    await waitFor(() => expect(mentionsApi.search).toHaveBeenCalledTimes(1));
    expect(mentionsApi.search).toHaveBeenCalledWith({ kind: 'chat', id: 'c1' }, 'jo sm');
  });

  it('shows an outsider the server returns, with the not-in-chat flag', async () => {
    openChat();
    mentionsApi.search.mockResolvedValue({
      agents: [],
      people: [{ id: 'u9', label: 'John Michael Smith', email: 'js@acme.test', inChat: false }],
    });
    const { result } = renderHook(() => useMentionables({ enabled: true, query: 'jo sm', assistantLabel: 'Assistant' }));
    await waitFor(() => expect(result.current.items.some((i) => i.group === 'others')).toBe(true));
    expect(result.current.items.find((i) => i.group === 'others')).toMatchObject({ label: 'John Michael Smith', email: 'js@acme.test', inChat: false });
    expect(result.current.settled).toBe(true);
  });

  it('offers the Add people row to someone who can invite, last, and to nobody else', async () => {
    openChat();
    const { result } = renderHook(() => useMentionables({ enabled: true, query: '', assistantLabel: 'Assistant' }));
    await waitFor(() => expect(result.current.items.at(-1)?.action).toBe('addPeople'));
    cleanup();
    useParticipantsStore.getState().reset();
    collab.getCollaborators.mockResolvedValue({ owner: view.owner, collaboratorCount: 3, myAccess: 'write' });
    const viewer = renderHook(() => useMentionables({ enabled: true, query: '', assistantLabel: 'Assistant' }));
    await waitFor(() => expect(viewer.result.current.items.length).toBeGreaterThan(0));
    await act(async () => {});
    expect(viewer.result.current.items.some((i) => i.action)).toBe(false);
  });

  it('on a new chat it asks the organization, counts the draft people as in the chat and offers Add people', async () => {
    const slot = useChatStore.getState().createSlot(null);
    useChatStore.getState().setActiveSlot(slot);
    useDraftShareStore.getState().add([{ type: 'user', id: 'u7', name: 'Dana Draft', level: 'write' }]);
    mentionsApi.search.mockResolvedValue({ agents: [], people: [{ id: 'u7', label: 'Dana Draft', inChat: true }] });
    const { result } = renderHook(() => useMentionables({ enabled: true, query: 'dana', assistantLabel: 'Assistant' }));
    await waitFor(() => expect(mentionsApi.search).toHaveBeenCalled());
    expect(mentionsApi.search).toHaveBeenCalledWith({ kind: 'new', include: ['u7'] }, 'dana');
    expect(collab.getCollaborators).not.toHaveBeenCalled();
    await waitFor(() => expect(result.current.items.map((i) => i.group)).toEqual(['people', 'action']));
    expect(result.current.items[0]).toMatchObject({ label: 'Dana Draft' });
  });

  it('a new chat opened for an agent does not use the organization scope', async () => {
    window.history.replaceState(null, '', '/chat/?agentId=agent-1');
    const slot = useChatStore.getState().createSlot(null);
    useChatStore.getState().setActiveSlot(slot);
    renderHook(() => useMentionables({ enabled: true, query: 'dana', assistantLabel: 'Assistant' }));
    await act(async () => {});
    expect(mentionsApi.search).not.toHaveBeenCalled();
  });
});
