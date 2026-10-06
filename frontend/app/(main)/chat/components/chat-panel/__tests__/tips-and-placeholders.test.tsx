/**
 * PR-10.6: composer placeholders for solo vs shared chats, and the first-send coachmarks. With
 * ENABLE_CHAT_MENTIONS off the wrapper must behave exactly as before: no placeholder override, no
 * coachmark, no tips request.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const append = vi.hoisted(() => vi.fn());
const api = vi.hoisted(() => ({ getPreferences: vi.fn(), markTipSeen: vi.fn() }));
const postNote = vi.hoisted(() => vi.fn());
const placeholders = vi.hoisted(() => ({ last: undefined as string | undefined }));

vi.mock('@assistant-ui/react', () => ({ useThreadRuntime: () => ({ append }) }));
vi.mock('@/chat/hooks/use-effective-agent-id', () => ({ useEffectiveAgentId: () => null }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({ fetchModelsForContext: () => Promise.resolve([]) }));
vi.mock('@/chat/api', () => ({ ChatApi: {} }));
vi.mock('@/chat/mentions/api', async (orig) => ({
  ...(await orig<object>()),
  MentionsApi: { postNote, listAgents: vi.fn().mockResolvedValue([]), list: vi.fn().mockResolvedValue({ items: [] }) },
}));
vi.mock('@/chat/utils/collab-send', async (orig) => ({
  ...(await orig<object>()),
  refreshFeedForSlot: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('@/lib/api', () => ({
  isRequestCancelledError: () => false,
  isSearchNoAccessibleDocumentsNotFound: () => false,
}));
vi.mock('@/app/(main)/notifications/api', () => ({
  NotificationsApi: api,
  TIP_IDS: [],
}));
vi.mock('../../chat-input', () => ({
  ChatInput: ({
    onSend,
    placeholder,
  }: {
    onSend: (m: string, a?: unknown, mentions?: unknown) => void;
    placeholder?: string;
  }) => {
    placeholders.last = placeholder;
    return (
      <div>
        <button type="button" onClick={() => onSend('hello')}>send</button>
        <button type="button" onClick={() => onSend('<@user:bob> hi', undefined, [{ type: 'user', id: 'bob' }])}>send-note</button>
        <button type="button" onClick={() => onSend('<@agent:a1> hi', undefined, [{ type: 'agent', id: 'a1' }])}>send-agent</button>
      </div>
    );
  },
}));

import { ChatInputWrapper } from '../chat-input-wrapper';
import { tipForSend } from '../use-send-coachmarks';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore } from '@/lib/store/user-store';
import { resetTipsStoreForTests } from '@/lib/store/tips-store';

const initialChat = useChatStore.getState();

function setup(opts: { mentions: boolean; collaborative: boolean; collaboratorCount?: number }) {
  useFeatureFlagsStore.setState({
    flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: opts.mentions },
  });
  useUserStore.setState({ profile: { userId: 'a' } as never });
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.setState({
    activeSlotId: slotId,
    conversations: opts.collaboratorCount === undefined ? [] : [{ id: 'conv-1', collaboratorCount: opts.collaboratorCount } as never],
  });
  useChatStore.getState().updateSlot(slotId, {
    access: {
      role: 'owner', isOwner: true, accessLevel: 'owner', canSend: true, canManage: true, canInvite: true,
      isCollaborative: opts.collaborative,
    },
  });
}

const mount = () => render(<Theme><ChatInputWrapper /></Theme>);

beforeEach(() => {
  vi.clearAllMocks();
  placeholders.last = undefined;
  resetTipsStoreForTests();
  useChatStore.setState({ ...initialChat, slots: {}, activeSlotId: null });
  api.getPreferences.mockResolvedValue({ tipsSeen: [] });
  api.markTipSeen.mockResolvedValue({ tipsSeen: [] });
  postNote.mockResolvedValue({ nonParticipants: [] });
});
afterEach(cleanup);

describe('tipForSend', () => {
  it.each([
    [undefined, true, 'mentions.firstSharedSend'],
    [[], true, 'mentions.firstSharedSend'],
    [[], false, null],
    [[{ type: 'assistant', id: 'self' }], true, 'mentions.firstSharedSend'],
    [[{ type: 'user', id: 'u' }], true, 'mentions.firstNote'],
    [[{ type: 'user', id: 'u' }, { type: 'team', id: 't' }], false, 'mentions.firstNote'],
    [[{ type: 'user', id: 'u' }, { type: 'assistant', id: 'self' }], true, 'mentions.firstSharedSend'],
    [[{ type: 'agent', id: 'a' }], false, 'mentions.firstAgentMention'],
    [[{ type: 'agent', id: 'a' }, { type: 'user', id: 'u' }], true, 'mentions.firstAgentMention'],
  ] as const)('%j shared=%s -> %s', (mentions, shared, expected) => {
    expect(tipForSend({ mentions: mentions as never, shared, respondMode: 'smart', sessionKind: 'chat' })).toBe(expected);
  });

  it('respondMode=always never yields firstNote', () => {
    const user = [{ type: 'user', id: 'u' }] as never;
    expect(tipForSend({ mentions: user, shared: true, respondMode: 'always', sessionKind: 'chat' })).toBe('mentions.firstSharedSend');
    expect(tipForSend({ mentions: user, shared: false, respondMode: 'always', sessionKind: 'agent' })).toBeNull();
  });

  it('mention_only follows the classifier', () => {
    const t = (mentions: unknown[], shared = true, sessionKind: 'chat' | 'agent' = 'chat') =>
      tipForSend({ mentions: mentions as never, shared, respondMode: 'mention_only', sessionKind });
    expect(t([])).toBe('mentions.firstNote');
    expect(t([{ type: 'user', id: 'u' }])).toBe('mentions.firstNote');
    expect(t([{ type: 'assistant', id: 'self' }])).toBe('mentions.firstSharedSend');
    expect(t([{ type: 'agent', id: 'a' }], false, 'agent')).toBe('mentions.firstAgentMention');
  });
});

describe('composer placeholder', () => {
  it('is the solo text in a solo chat and the shared text in a shared chat', () => {
    setup({ mentions: true, collaborative: false });
    mount();
    expect(placeholders.last).toBe('Ask anything · type @ for agents');
    cleanup();
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    expect(placeholders.last).toBe('Ask PipesHub, or type @ to mention a teammate or agent');
  });

  it('is not overridden with the flag off, solo or shared', () => {
    setup({ mentions: false, collaborative: true, collaboratorCount: 2 });
    mount();
    expect(placeholders.last).toBeUndefined();
  });
});

describe('first-send coachmarks', () => {
  it('shows the audience size after the first send in a shared chat, once', async () => {
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('coachmark-mentions.firstSharedSend')).toBeNull();

    fireEvent.click(screen.getByText('send'));
    const mark = await screen.findByTestId('coachmark-mentions.firstSharedSend');
    expect(mark.textContent).toContain('Your message and the answer are visible to 3 people');

    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(api.markTipSeen).toHaveBeenCalledExactlyOnceWith('mentions.firstSharedSend');
    await waitFor(() => expect(screen.queryByTestId('coachmark-mentions.firstSharedSend')).toBeNull());

    fireEvent.click(screen.getByText('send'));
    expect(screen.queryByTestId('coachmark-mentions.firstSharedSend')).toBeNull();
    expect(api.markTipSeen).toHaveBeenCalledTimes(1);
  });

  it('never shows a tip the server says was seen', async () => {
    api.getPreferences.mockResolvedValue({ tipsSeen: ['mentions.firstSharedSend'] });
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    fireEvent.click(screen.getByText('send'));
    expect(screen.queryByTestId('coachmark-mentions.firstSharedSend')).toBeNull();
  });

  it('shows the note tip for a people-only mention and the agent tip for an agent mention', async () => {
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    fireEvent.click(screen.getByText('send-note'));
    expect((await screen.findByTestId('coachmark-mentions.firstNote')).textContent).toContain("Notes don't ask the AI");
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    await waitFor(() => expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull());

    fireEvent.click(screen.getByText('send-agent'));
    expect((await screen.findByTestId('coachmark-mentions.firstAgentMention')).textContent).toContain('answers with your access');
  });

  it('waits while the add-to-chat prompt is open, so the tip never covers it', async () => {
    postNote.mockResolvedValue({ nonParticipants: ['bob'] });
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    fireEvent.click(screen.getByText('send-note'));
    await screen.findByTestId('non-participant-prompt');
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    expect((await screen.findByTestId('coachmark-mentions.firstNote')).textContent).toContain("Notes don't ask the AI");
  });

  it('waits while the busy banner is up (another person is asking, or a send is queued), so the tip never covers it', async () => {
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    const slotId = useChatStore.getState().activeSlotId!;
    useChatStore.getState().updateSlot(slotId, {
      activeRun: { runId: 'r1', userId: 'b', displayName: 'Bob', startedAt: 0 } as never,
      isStreaming: false,
    });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    fireEvent.click(screen.getByText('send'));
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId('coachmark-mentions.firstSharedSend')).toBeNull();
    useChatStore.getState().updateSlot(slotId, { activeRun: null, queuedSend: null } as never);
    expect((await screen.findByTestId('coachmark-mentions.firstSharedSend')).textContent).toContain('visible to 3 people');
  });

  it('shows no note tip when posting the note fails', async () => {
    postNote.mockRejectedValue(new Error('boom'));
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    fireEvent.click(screen.getByText('send-note'));
    await waitFor(() => expect(postNote).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull();
    expect(append).not.toHaveBeenCalled();
  });

  it('hides at once, before the server confirms', async () => {
    api.markTipSeen.mockReturnValue(new Promise(() => undefined));
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    fireEvent.click(screen.getByText('send'));
    await screen.findByTestId('coachmark-mentions.firstSharedSend');
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    await waitFor(() => expect(screen.queryByTestId('coachmark-mentions.firstSharedSend')).toBeNull());
  });

  it('stays hidden when the tips list cannot be loaded', async () => {
    api.getPreferences.mockRejectedValue(new Error('down'));
    setup({ mentions: true, collaborative: true, collaboratorCount: 2 });
    mount();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    fireEvent.click(screen.getByText('send'));
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId('coachmark-mentions.firstSharedSend')).toBeNull();
  });

  it('flag off: no coachmark, no tips request, no write', async () => {
    setup({ mentions: false, collaborative: true, collaboratorCount: 2 });
    mount();
    fireEvent.click(screen.getByText('send'));
    await new Promise((r) => setTimeout(r, 10));
    expect(document.querySelector('[data-testid^="coachmark-"]')).toBeNull();
    expect(api.getPreferences).not.toHaveBeenCalled();
    expect(api.markTipSeen).not.toHaveBeenCalled();
    expect(document.querySelector('[aria-haspopup="dialog"]')).toBeNull();
  });
});
