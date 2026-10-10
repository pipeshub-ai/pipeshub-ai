/**
 * Notes and mentions at the send boundary (PR-10.3): a note bypasses the run block and send-when-free
 * (MN-10), everything else goes to the runtime with its mentions, and a mention of someone outside the
 * chat is offered to the owner as "add them" (MN-11, MN-12).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const append = vi.hoisted(() => vi.fn());
const api = vi.hoisted(() => ({
  postNote: vi.fn(),
  getCollaborators: vi.fn(),
  putCollaborators: vi.fn(),
  refreshFeed: vi.fn(),
}));
const sendArgs = vi.hoisted(() => ({
  value: ['hi'] as [string, unknown?, unknown?],
}));

vi.mock('@assistant-ui/react', () => ({ useThreadRuntime: () => ({ append }) }));
vi.mock('@/chat/hooks/use-effective-agent-id', () => ({ useEffectiveAgentId: () => null }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({ fetchModelsForContext: () => Promise.resolve([]) }));
vi.mock('@/chat/api', () => ({ ChatApi: {} }));
vi.mock('@/lib/api', () => ({
  isRequestCancelledError: () => false,
  isSearchNoAccessibleDocumentsNotFound: () => false,
}));
vi.mock('@/chat/mentions/api', () => ({ MentionsApi: { postNote: api.postNote, listAgents: vi.fn().mockResolvedValue([]) } }));
vi.mock('@/chat/collaboration-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/chat/collaboration-api')>()),
  CollaborationApi: { getCollaborators: api.getCollaborators, putCollaborators: api.putCollaborators },
}));
vi.mock('@/chat/utils/collab-send', () => ({ refreshFeedForSlot: api.refreshFeed }));
vi.mock('../../chat-input', () => ({
  ChatInput: ({ onSend }: { onSend: (m: string, a?: unknown, mentions?: unknown) => void }) => (
    <button type="button" onClick={() => onSend(...sendArgs.value)}>
      send
    </button>
  ),
}));

import { ChatInputWrapper } from '../chat-input-wrapper';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useToastStore } from '@/lib/store/toast-store';
import { useParticipantsStore } from '@/chat/mentions/participants-store';

const initialState = useChatStore.getState();
const BOB = { type: 'user' as const, id: 'u-bob' };
const DAN = { type: 'user' as const, id: 'u-dan' };
const ASSISTANT = { type: 'assistant' as const, id: 'self' };

const view = (over: Record<string, unknown> = {}) => ({
  owner: { userId: 'owner-1', displayName: 'Olive Owner' },
  collaborators: [{ principalType: 'user', principalId: 'u-bob', displayName: 'Bob Builder', accessLevel: 'write', state: 'active' }],
  collaboratorCount: 1,
  settings: { editorsCanInvite: false, ownerContentShared: false },
  ...over,
});

function openSlot(over: Record<string, unknown> = {}) {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, {
    isInitialized: true,
    access: { role: 'owner', isOwner: true, accessLevel: 'owner', canSend: true, canManage: true, canInvite: true, isCollaborative: true },
    ...over,
  });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

async function mount() {
  render(
    <Theme>
      <ChatInputWrapper />
    </Theme>,
  );
  await waitFor(() => expect(useParticipantsStore.getState().byConv['conv-1']).toBeTruthy());
}

const press = () => fireEvent.click(screen.getByRole('button', { name: 'send' }));

beforeEach(() => {
  append.mockReset();
  api.postNote.mockReset();
  api.refreshFeed.mockReset();
  api.putCollaborators.mockReset();
  api.getCollaborators.mockReset();
  api.getCollaborators.mockResolvedValue(view());
  api.postNote.mockResolvedValue({ note: { id: 'n1', seq: 3 }, duplicate: false, nonParticipants: [] });
  api.putCollaborators.mockResolvedValue(view());
  useParticipantsStore.getState().reset();
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useToastStore.setState({ toasts: [] });
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: true } } as never);
  sendArgs.value = ['hi'];
});

afterEach(() => {
  cleanup();
  useFeatureFlagsStore.setState({ flags: null });
});

describe('a note', () => {
  it('MN-10: is posted while someone else is running, and the runtime is not touched', async () => {
    openSlot({ isStreaming: true, activeRun: { userId: 'u2', displayName: 'Ann', startedAt: '2026-10-01T00:00:00.000Z', runId: 'r1' } });
    sendArgs.value = ['<@user:u-bob> fyi', undefined, [BOB]];
    await mount();
    press();
    await waitFor(() => expect(api.postNote).toHaveBeenCalledTimes(1));
    expect(api.postNote).toHaveBeenCalledWith(
      { kind: 'chat', id: 'conv-1' },
      { query: '<@user:u-bob> fyi', mentions: [BOB], clientMessageId: expect.any(String) },
    );
    await waitFor(() => expect(api.refreshFeed).toHaveBeenCalled());
    expect(append).not.toHaveBeenCalled();
    expect(useChatStore.getState().slots[useChatStore.getState().activeSlotId!].queuedSend).toBeNull();
  });

  it('uses the agent route in an agent chat, and the mode the summary view reports', async () => {
    openSlot({ threadAgentId: 'agent-7' });
    api.getCollaborators.mockResolvedValue({
      owner: { userId: 'owner-1', displayName: 'Olive Owner' },
      collaboratorCount: 1,
      myAccess: 'write',
      respondMode: 'mention_only',
    });
    sendArgs.value = ['<@user:u-bob> fyi', undefined, [BOB]];
    await mount();
    press();
    await waitFor(() => expect(api.postNote).toHaveBeenCalled());
    expect(api.postNote.mock.calls[0][0]).toEqual({ kind: 'agent', agentKey: 'agent-7', id: 'conv-1' });
  });

  it('MN-09: under mention_only a message that mentions nobody is posted as a note, never sent to the stream or dropped', async () => {
    openSlot();
    api.getCollaborators.mockResolvedValue(view({ settings: { editorsCanInvite: false, ownerContentShared: false, respondMode: 'mention_only' } }));
    sendArgs.value = ['just thinking out loud'];
    await mount();
    press();
    await waitFor(() => expect(api.postNote).toHaveBeenCalledTimes(1));
    expect(api.postNote.mock.calls[0][1]).toEqual({ query: 'just thinking out loud', mentions: [], clientMessageId: expect.any(String) });
    expect(append).not.toHaveBeenCalled();
  });

  it('under smart a message that mentions nobody still goes to the AI', async () => {
    openSlot();
    sendArgs.value = ['what changed?'];
    await mount();
    press();
    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(api.postNote).not.toHaveBeenCalled();
  });

  it('is not a note under respondMode always: the AI runs and the mentions go with it', async () => {
    openSlot();
    api.getCollaborators.mockResolvedValue(view({ settings: { editorsCanInvite: false, ownerContentShared: false, respondMode: 'always' } }));
    sendArgs.value = ['<@user:u-bob> fyi', undefined, [BOB]];
    await mount();
    press();
    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(api.postNote).not.toHaveBeenCalled();
  });

  it('with an attachment is refused with a reason and the text comes back', async () => {
    const slotId = openSlot();
    sendArgs.value = ['<@user:u-bob> fyi', [{ recordId: 'r', virtualRecordId: 'v', recordName: 'a.pdf', mimeType: 'application/pdf', extension: 'pdf' }], [BOB]];
    await mount();
    press();
    await waitFor(() => expect(useChatStore.getState().slots[slotId].composerRestore).toBe('<@user:u-bob> fyi'));
    expect(api.postNote).not.toHaveBeenCalled();
    expect(append).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts.length).toBeGreaterThan(0);
  });

  it('a failed post says so and puts the text back', async () => {
    const slotId = openSlot();
    api.postNote.mockRejectedValue(new Error('boom'));
    sendArgs.value = ['<@user:u-bob> fyi', undefined, [BOB]];
    await mount();
    press();
    await waitFor(() => expect(useChatStore.getState().slots[slotId].composerRestore).toBe('<@user:u-bob> fyi'));
    expect(useToastStore.getState().toasts.length).toBeGreaterThan(0);
  });
});

describe('a message for the assistant', () => {
  it('goes to the runtime with its mentions', async () => {
    openSlot();
    sendArgs.value = ['@assistant ask <@user:u-bob>', undefined, [ASSISTANT, BOB]];
    await mount();
    press();
    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(append.mock.calls[0][0].metadata.custom.mentions).toEqual([ASSISTANT, BOB]);
    expect(api.postNote).not.toHaveBeenCalled();
  });

  it('is still blocked while this tab streams', async () => {
    openSlot({ isStreaming: true });
    sendArgs.value = ['@assistant hi', undefined, [ASSISTANT]];
    await mount();
    press();
    expect(append).not.toHaveBeenCalled();
  });

  it('a plain message with no mentions is the unchanged path', async () => {
    openSlot();
    sendArgs.value = ['just a question'];
    await mount();
    press();
    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(append.mock.calls[0][0].metadata.custom.mentions).toBeUndefined();
  });
});

describe('flag off', () => {
  it('mentions are not classified: the message goes to the runtime and nothing is fetched', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: false } } as never);
    openSlot();
    sendArgs.value = ['<@user:u-bob> fyi', undefined, [BOB]];
    render(
      <Theme>
        <ChatInputWrapper />
      </Theme>,
    );
    press();
    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(api.postNote).not.toHaveBeenCalled();
    expect(api.getCollaborators).not.toHaveBeenCalled();
  });
});

describe('MN-11 / MN-12: someone mentioned who is not in the chat', () => {
  it('the owner is offered to add them; nothing is shared until a choice, then the collaborators API adds them', async () => {
    openSlot();
    api.postNote.mockResolvedValue({ note: { id: 'n1', seq: 3 }, duplicate: false, nonParticipants: ['u-dan'] });
    sendArgs.value = ['<@user:u-bob> <@user:u-dan> fyi', undefined, [BOB, DAN]];
    await mount();
    useParticipantsStore.getState().remember([{ ref: DAN, label: 'Dan Doe' }]);
    press();
    await waitFor(() => expect(screen.getByTestId('non-participant-prompt')).toBeTruthy());
    expect(screen.getByText('Add Dan Doe to this chat?')).toBeTruthy();
    expect(api.putCollaborators).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Can view' }));
    await waitFor(() =>
      expect(api.putCollaborators).toHaveBeenCalledWith(
        { kind: 'chat', id: 'conv-1' },
        { collaborators: [{ principalType: 'user', principalId: 'u-dan', accessLevel: 'read' }] },
      ),
    );
    await waitFor(() => expect(screen.queryByTestId('non-participant-prompt')).toBeNull());
  });

  it('"Not now" closes the offer without sharing', async () => {
    openSlot();
    api.postNote.mockResolvedValue({ note: { id: 'n1', seq: 3 }, duplicate: false, nonParticipants: ['u-dan'] });
    sendArgs.value = ['<@user:u-dan> fyi', undefined, [DAN]];
    await mount();
    press();
    await waitFor(() => expect(screen.getByTestId('non-participant-prompt')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    expect(screen.queryByTestId('non-participant-prompt')).toBeNull();
    expect(api.putCollaborators).not.toHaveBeenCalled();
  });

  it('someone who cannot invite is told to ask the owner, with no add button', async () => {
    openSlot();
    api.getCollaborators.mockResolvedValue({ owner: { userId: 'owner-1', displayName: 'Olive Owner' }, collaboratorCount: 1, myAccess: 'write' });
    api.postNote.mockResolvedValue({ note: { id: 'n1', seq: 3 }, duplicate: false, nonParticipants: ['u-dan'] });
    sendArgs.value = ['<@user:u-dan> fyi', undefined, [DAN]];
    await mount();
    press();
    await waitFor(() => expect(screen.getByTestId('non-participant-prompt')).toBeTruthy());
    expect(screen.getByText(/isn't in this chat\. Ask the owner to add them\./)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Can view' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Can continue' })).toBeNull();
  });

  it('names the outsider by what the picker showed (an organization search result), never an empty label', async () => {
    openSlot();
    sendArgs.value = ['@assistant tell <@user:u-dan>', undefined, [ASSISTANT, DAN]];
    await mount();
    // What picking a "Not in this chat" row stores (see rememberMentionLabels in the composer).
    useParticipantsStore.getState().remember([{ ref: DAN, label: 'Dana Outsider' }]);
    press();
    await waitFor(() => expect(screen.getByText('Add Dana Outsider to this chat?')).toBeTruthy());
  });

  it('a stream message that mentions an outsider gets the same offer when the full list is known', async () => {
    openSlot();
    sendArgs.value = ['@assistant tell <@user:u-dan>', undefined, [ASSISTANT, DAN]];
    await mount();
    press();
    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('non-participant-prompt')).toBeTruthy());
  });
});
