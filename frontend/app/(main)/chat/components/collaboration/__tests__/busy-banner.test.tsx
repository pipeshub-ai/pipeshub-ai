import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, act, renderHook } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import {
  installMemoryStorage,
  jwtExpiringIn,
  sseFrame,
  sseResponse,
} from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

vi.mock('../../../utils/fetch-models-for-context', () => ({
  fetchModelsForContext: vi.fn().mockResolvedValue(undefined),
}));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../../../store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useUserStore } = await import('@/lib/store/user-store');
const { ChatApi } = await import('../../../api');
const { BusyBanner } = await import('../busy-banner');
const { applyFeedPage } = await import('../../../utils/apply-feed-page');
const { queueSend, shouldQueueSend, cancelQueuedSend } = await import('../../../utils/queued-send');
const { useQueuedSendComposer } = await import('../../../hooks/use-queued-send-composer');
const { loadDraft } = await import('../../../utils/draft-storage');
const { buildExternalStoreConfig } = await import('../../../runtime');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();

const BOB = { userId: 'u2', displayName: 'Bob', startedAt: '2026-10-01T00:00:00.000Z', runId: 'run-1' };

function setFlag(on: boolean) {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as never);
}

function seed(extra: Record<string, unknown> = {}) {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, {
    isInitialized: true,
    access: {
      role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false,
      isCollaborative: true,
    },
    messages: [{ id: 'u1', role: 'user', content: [{ type: 'text', text: 'q' }], metadata: { custom: { seq: 0, rev: 1 } } }],
    rev: 1,
    ...extra,
  });
  useChatStore.setState({ activeSlotId: slotId });
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];

function renderBanner(slotId: string) {
  return render(
    <Theme>
      <BusyBanner slotId={slotId} />
    </Theme>,
  );
}

const frame = (type: string, fields: Record<string, unknown> = {}) => sseFrame(type, { type, ...fields });

function bobFinishedPage(rev: number) {
  const msg = (id: string, seq: number, type: 'user_query' | 'bot_response', content: string) => ({
    _id: id, messageType: type, content, contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], feedback: [],
    createdAt: 'x', updatedAt: 'x', seq,
  });
  return {
    messages: [msg('u2', 1, 'user_query', 'bob asks'), msg('a2', 2, 'bot_response', 'bob answer')],
    rev, nextSeq: 2, hasMore: false, activeRun: null, lastActivityAt: 1,
  } as never;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useUserStore.setState({ profile: { userId: 'me' } as never });
  window.localStorage.clear();
  setFlag(true);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the busy banner', () => {
  it('says who is asking and offers Stop to someone who can send', () => {
    renderBanner(seed({ activeRun: BOB }));
    expect(screen.getByTestId('busy-banner').textContent).toContain('Bob is asking…');
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy();
  });

  it('hides Stop from a viewer and when the run id is not shared', () => {
    const slotId = seed({ activeRun: BOB, access: { role: 'read', isOwner: false, accessLevel: 'read', canSend: false, canManage: false, canInvite: false, isCollaborative: true } });
    renderBanner(slotId);
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
    cleanup();
    renderBanner(seed({ activeRun: { ...BOB, runId: undefined } }));
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
  });

  it('Stop cancels that run', () => {
    const cancel = vi.spyOn(ChatApi, 'cancelStream').mockResolvedValue({ cancelled: true });
    renderBanner(seed({ activeRun: BOB, threadAgentId: 'agent-1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(cancel).toHaveBeenCalledWith('conv-1', 'run-1', 'agent-1');
  });

  it('says so when Stop fails instead of failing silently', async () => {
    const { useToastStore } = await import('@/lib/store/toast-store');
    useToastStore.getState().clearAll();
    vi.spyOn(ChatApi, 'cancelStream').mockRejectedValue({ statusCode: 403, code: 'CONVERSATION_READ_ONLY' });
    renderBanner(seed({ activeRun: BOB }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await act(async () => {});
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0].variant).toBe('error');
  });

  it('says "asking in another tab" for the user\'s own run when this tab is not streaming', () => {
    renderBanner(seed({ activeRun: { ...BOB, userId: 'me' } }));
    expect(screen.getByTestId('busy-banner').textContent).toContain("You're asking in another tab");
  });

  it('shows nothing when nobody is running, while streaming here, with the flag off, or once access is lost', () => {
    const slotId = seed();
    const { container } = renderBanner(slotId);
    expect(screen.queryByTestId('busy-banner')).toBeNull();
    act(() => useChatStore.getState().updateSlot(slotId, { activeRun: BOB, isStreaming: true }));
    expect(screen.queryByTestId('busy-banner')).toBeNull();
    act(() => useChatStore.getState().updateSlot(slotId, { isStreaming: false }));
    expect(screen.getByTestId('busy-banner')).toBeTruthy();
    act(() => useChatStore.getState().updateSlot(slotId, { accessLost: true }));
    expect(container.textContent).toBe('');
    act(() => useChatStore.getState().updateSlot(slotId, { accessLost: false }));
    act(() => setFlag(false));
    expect(container.textContent).toBe('');
  });

  it('uses a generic name when the server sent none', () => {
    renderBanner(seed({ activeRun: { ...BOB, displayName: '' } }));
    expect(screen.getByTestId('busy-banner').textContent).toContain('Someone is asking…');
  });
});

describe('announcements (UX-06)', () => {
  it('announces once per run, not on every poll', () => {
    const slotId = seed({ activeRun: BOB });
    renderBanner(slotId);
    const live = screen.getByTestId('busy-announcer');
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(live.textContent).toContain('Bob is asking');
    const node = live.firstChild;

    act(() => useChatStore.getState().updateSlot(slotId, { activeRun: { ...BOB } }));
    act(() => useChatStore.getState().updateSlot(slotId, { activeRun: { ...BOB, displayName: 'Robert' } }));
    expect(live.firstChild).toBe(node);
    expect(live.textContent).toContain('Bob is asking');

    act(() => useChatStore.getState().updateSlot(slotId, { activeRun: { ...BOB, runId: 'run-2', displayName: 'Carol' } }));
    expect(live.textContent).toContain('Carol is asking');
  });

  it('clears the announcement when the run ends', () => {
    const slotId = seed({ activeRun: BOB });
    renderBanner(slotId);
    act(() => useChatStore.getState().updateSlot(slotId, { activeRun: null }));
    expect(screen.getByTestId('busy-announcer').textContent).toBe('');
  });
});

describe('send when free (FE-13)', () => {
  const chatStream = () => {
    fetchMock.mockImplementationOnce(async () =>
      sseResponse([
        frame('RUN_FINISHED', {
          result: {
            conversation: {
              _id: 'conv-1', title: 't', createdAt: 'x', updatedAt: 'x', isShared: true, status: 'complete',
              modelInfo: { modelKey: 'm1', modelName: 'gpt-5' },
              messages: [],
            },
          },
        }),
      ]),
    );
  };

  it('a send while someone is running is held, not posted', () => {
    const slotId = seed({ activeRun: BOB });
    expect(shouldQueueSend(slot(slotId))).toBe(true);
    queueSend(slotId, { query: 'when you are done' });
    renderBanner(slotId);
    expect(slot(slotId).queuedSend).toMatchObject({ query: 'when you are done' });
    expect(screen.getByTestId('busy-banner').textContent).toContain('Waiting for Bob to finish. Your message will be sent automatically.');
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(loadDraft('conv-1')).toBe('when you are done');
  });

  it('sends once, with the baseSeq after the sync merged, when activeRun clears', async () => {
    chatStream();
    const slotId = seed({ activeRun: BOB });
    queueSend(slotId, { query: 'when you are done' });
    const { clientMessageId } = slot(slotId).queuedSend!;
    renderBanner(slotId);
    expect(fetchMock).not.toHaveBeenCalled();

    await act(async () => {
      applyFeedPage(slotId, bobFinishedPage(2));
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ query: 'when you are done', baseSeq: 2, clientMessageId });
    expect(slot(slotId).queuedSend).toBeNull();

    await act(async () => {
      applyFeedPage(slotId, bobFinishedPage(3));
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not send while still busy, and a second queued message replaces the first', () => {
    const slotId = seed({ activeRun: BOB });
    queueSend(slotId, { query: 'one' });
    queueSend(slotId, { query: 'two' });
    renderBanner(slotId);
    act(() => useChatStore.getState().updateSlot(slotId, { activeRun: { ...BOB, runId: 'run-2' } }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(slot(slotId).queuedSend?.query).toBe('two');
  });

  it('Cancel drops the queue and puts the text back in the composer', () => {
    const slotId = seed({ activeRun: BOB });
    queueSend(slotId, { query: 'changed my mind' });
    renderBanner(slotId);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(slot(slotId).queuedSend).toBeNull();
    expect(slot(slotId).composerRestore).toBe('changed my mind');
    act(() => useChatStore.getState().updateSlot(slotId, { activeRun: null }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('an edit in the composer cancels the auto-send', async () => {
    const slotId = seed({ activeRun: BOB });
    queueSend(slotId, { query: 'queued text' });
    renderBanner(slotId);
    const { rerender } = renderHook(({ text }) => useQueuedSendComposer(slotId, text, vi.fn()), {
      initialProps: { text: '' },
    });
    rerender({ text: 'let me rewrite this' });
    expect(slot(slotId).queuedSend).toBeNull();
    await act(async () => {
      applyFeedPage(slotId, bobFinishedPage(2));
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(loadDraft('conv-1')).toBe('queued text');
  });

  it('is cancelled when the user leaves the chat or loses access', () => {
    const slotId = seed({ activeRun: BOB });
    queueSend(slotId, { query: 'x' });
    cancelQueuedSend(slotId);
    expect(slot(slotId).queuedSend).toBeNull();
    cancelQueuedSend(slotId);
    expect(slot(slotId).queuedSend).toBeNull();
  });

  it('holds nothing when the flag is off, the user cannot send, or nobody is busy', () => {
    const slotId = seed({ activeRun: BOB });
    setFlag(false);
    expect(shouldQueueSend(slot(slotId))).toBe(false);
    setFlag(true);
    expect(shouldQueueSend({ ...slot(slotId), activeRun: null })).toBe(false);
    expect(shouldQueueSend({ ...slot(slotId), isStreaming: true })).toBe(false);
    expect(shouldQueueSend({ ...slot(slotId), accessLost: true })).toBe(false);
    expect(shouldQueueSend({ ...slot(slotId), access: { ...slot(slotId).access!, canSend: false } })).toBe(false);
  });

  it('a queued attachment-only message goes out with its attachments', async () => {
    chatStream();
    const slotId = seed({ activeRun: BOB });
    const attachment = { recordId: 'rec-1', recordName: 'a.pdf', mimeType: 'application/pdf' } as never;
    queueSend(slotId, { query: '', attachments: [attachment] });
    renderBanner(slotId);
    await act(async () => {
      applyFeedPage(slotId, bobFinishedPage(2));
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.query).toBe('See below attached file(s).');
    expect(body.attachments).toHaveLength(1);
  });
});

describe('pressing send while someone else is running', () => {
  const message = { role: 'user', content: [{ type: 'text', text: 'my turn' }] } as never;

  it('queues the message instead of posting it', async () => {
    const slotId = seed({ activeRun: BOB });
    await buildExternalStoreConfig(slotId).onNew(message);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(slot(slotId).queuedSend).toMatchObject({ query: 'my turn' });
    expect(slot(slotId).messages).toHaveLength(1);
  });

  it('posts it as before with the flag off', async () => {
    setFlag(false);
    fetchMock.mockResolvedValue(sseResponse([]));
    const slotId = seed({ activeRun: BOB });
    await buildExternalStoreConfig(slotId).onNew(message);
    expect(slot(slotId).queuedSend).toBeNull();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });
});

describe('after a rejected send (CL-21)', () => {
  it('lets the text wrap below a readable width so the buttons drop under it on narrow screens', () => {
    renderBanner(seed({
      changedNotice: { count: 2, pending: { query: 'q', clientMessageId: 'cm-1', queuedAt: 1 } },
      activeRun: BOB,
    }));
    for (const id of ['changed-notice', 'busy-banner']) {
      const text = screen.getByTestId(id).querySelector('span.rt-Text') as HTMLElement;
      expect(text.style.flex).toBe('1 1 12rem');
      expect(text.style.minWidth).toMatch(/^0(px)?$/);
    }
  });

  it('shows how many messages arrived and lets the user resend or edit', async () => {
    const slotId = seed({
      changedNotice: { count: 2, pending: { query: 'my reply', clientMessageId: 'cm-1', queuedAt: 1 } },
    });
    renderBanner(slotId);
    expect(screen.getByTestId('changed-notice').textContent).toContain('2 new messages above');

    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    expect(slot(slotId).composerRestore).toBe('my reply');
    expect(slot(slotId).changedNotice).toBeNull();
  });

  it('uses the singular for one message and resends the held message', async () => {
    fetchMock.mockImplementationOnce(async () => sseResponse([frame('RUN_FINISHED', { result: { conversation: { _id: 'conv-1', title: 't', createdAt: 'x', updatedAt: 'x', isShared: true, status: 'complete', modelInfo: { modelKey: 'm', modelName: 'n' }, messages: [] } } })]));
    const slotId = seed({
      changedNotice: { count: 1, pending: { query: 'my reply', clientMessageId: 'cm-1', queuedAt: 1 } },
    });
    renderBanner(slotId);
    expect(screen.getByTestId('changed-notice').textContent).toContain('1 new message above');
    fireEvent.click(screen.getByRole('button', { name: 'Send again' }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ query: 'my reply', clientMessageId: 'cm-1' });
    expect(slot(slotId).changedNotice).toBeNull();
  });

  it('shows only the count when there is nothing to resend', () => {
    renderBanner(seed({ changedNotice: { count: 3, pending: null } }));
    expect(screen.getByTestId('changed-notice').textContent).toContain('3 new messages above');
    expect(screen.queryByRole('button', { name: 'Send again' })).toBeNull();
  });
});

describe('the composer binding', () => {
  it('puts restored text back only into an empty composer, once', () => {
    const slotId = seed({ composerRestore: 'restored' });
    const setText = vi.fn();
    renderHook(() => useQueuedSendComposer(slotId, '', setText));
    expect(setText).toHaveBeenCalledWith('restored');
    expect(slot(slotId).composerRestore).toBeNull();

    const slotId2 = seed({ composerRestore: 'restored' });
    const setText2 = vi.fn();
    renderHook(() => useQueuedSendComposer(slotId2, 'typing already', setText2));
    expect(setText2).not.toHaveBeenCalled();
    expect(slot(slotId2).composerRestore).toBeNull();
  });

  it('keeps what was typed in a composer removed because access was lost', () => {
    const slotId = seed();
    const { unmount } = renderHook(() => useQueuedSendComposer(slotId, 'half a thought', vi.fn()));
    act(() => useChatStore.getState().updateSlot(slotId, { accessLost: true }));
    unmount();
    expect(loadDraft('conv-1')).toBe('half a thought');
  });

  it('keeps nothing when the composer goes away normally', () => {
    const slotId = seed();
    const { unmount } = renderHook(() => useQueuedSendComposer(slotId, 'text', vi.fn()));
    unmount();
    expect(loadDraft('conv-1')).toBeNull();
  });

  it('ignores a missing slot', () => {
    const { unmount } = renderHook(() => useQueuedSendComposer(null, 'text', vi.fn()));
    unmount();
  });
});
