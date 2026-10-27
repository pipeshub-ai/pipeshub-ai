/**
 * Asking while the answer streams, with the default chat extension: the question joins the queue listed above the
 * box instead of being refused, and the box is free for the next one. Each queued question can be edited back into
 * the box, removed, or all cleared.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const captured = vi.hoisted(() => ({ props: null as null | Record<string, unknown> }));
const runtime = vi.hoisted(() => ({ append: vi.fn() }));

// The default extension, read once loaded: importing it from the factory would load the chat modules against the real
// `@/config` instead of this one.
const config = vi.hoisted(() => ({ chatExtension: null as unknown, chatSlots: null as unknown }));
vi.mock('@/config', () => ({
  useAuthStore: { getState: () => ({}) },
  logoutAndRedirect: vi.fn(),
  get chatExtension() {
    return config.chatExtension;
  },
  get chatSlots() {
    return config.chatSlots;
  },
}));

vi.mock('@assistant-ui/react', () => ({
  useThreadRuntime: () => runtime,
}));

vi.mock('../../chat-input', () => ({
  ChatInput: (props: Record<string, unknown>) => {
    captured.props = props;
    return null;
  },
}));

vi.mock('@/chat/hooks/use-effective-agent-id', () => ({
  useEffectiveAgentId: () => null,
}));

vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: () => Promise.resolve(),
}));

const { defaultChatExtension, defaultChatSlots } = await import('../../../chat-extension');
config.chatExtension = defaultChatExtension;
config.chatSlots = defaultChatSlots;
const { ChatInputWrapper } = await import('../chat-input-wrapper');
const { useChatStore } = await import('@/chat/store');
const { useToastStore } = await import('@/lib/store/toast-store');
const { MAX_QUEUED_SENDS, queueOf, queueSend } = await import('@/chat/utils/queued-send');

const initialChat = useChatStore.getState();

function streamingChat() {
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, { isInitialized: true, isOwner: true, isStreaming: true });
  store.setActiveSlot(slotId);
  return slotId;
}

const slot = (id: string) => useChatStore.getState().slots[id];

function mount() {
  render(
    <Theme>
      <ChatInputWrapper />
    </Theme>,
  );
  return captured.props?.onSend as (message: string, attachments?: unknown) => Promise<unknown>;
}

beforeEach(() => {
  window.sessionStorage.clear();
  useToastStore.setState({ toasts: [] });
});

afterEach(() => {
  cleanup();
  runtime.append.mockReset();
  useChatStore.setState(initialChat, true);
});

describe('ChatInputWrapper: the questions queue', () => {
  it('tells the composer that sends queue while the answer runs', () => {
    streamingChat();
    mount();
    expect(captured.props?.queueDuringRun).toBe(true);
  });

  it('queues a question asked while your answer streams, clears the box, and lists it above', async () => {
    const slotId = streamingChat();
    const onSend = mount();

    await act(async () => {
      await expect(onSend('And for Q4?')).resolves.toBeUndefined();
    });

    expect(runtime.append).not.toHaveBeenCalled();
    expect(queueOf(slot(slotId)).map((q) => q.query)).toEqual(['And for Q4?']);
    const list = screen.getByTestId('queue-list');
    expect(list.textContent).toContain('Queued · 1');
    expect(list.textContent).toContain('And for Q4?');
  });

  it('sends at once when nothing runs and nothing waits', async () => {
    const slotId = streamingChat();
    useChatStore.getState().updateSlot(slotId, { isStreaming: false });
    const onSend = mount();

    await act(async () => {
      await onSend('Right away');
    });

    expect(runtime.append).toHaveBeenCalledOnce();
    expect(queueOf(slot(slotId))).toEqual([]);
    expect(screen.queryByTestId('queue-list')).toBeNull();
  });

  it('keeps the question in the box when the queue is full', async () => {
    const slotId = streamingChat();
    for (let i = 0; i < MAX_QUEUED_SENDS; i += 1) queueSend(slotId, { query: `q ${i}` });
    const onSend = mount();

    await act(async () => {
      await expect(onSend('One more')).resolves.toBe(false);
    });

    expect(queueOf(slot(slotId))).toHaveLength(MAX_QUEUED_SENDS);
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it('takes five at most', () => {
    expect(MAX_QUEUED_SENDS).toBe(5);
  });

  it('folds down to its title and opens again, keeping what waits', () => {
    const slotId = streamingChat();
    ['first', 'second'].forEach((query) => queueSend(slotId, { query }));
    mount();
    const toggle = screen.getByTestId('queue-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getAllByTestId('queued-row')).toHaveLength(2);

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-label')).toBe('Show queued messages');
    expect(screen.getByRole('list', { hidden: true }).hidden).toBe(true);
    expect(screen.getByTestId('queue-list').textContent).toContain('Queued · 2');

    fireEvent.click(toggle);
    expect(screen.getByRole('list').hidden).toBe(false);
    expect(queueOf(slot(slotId)).map((q) => q.query)).toEqual(['first', 'second']);
  });

  it('edits one back into an empty box, removes one, and clears them all', () => {
    const slotId = streamingChat();
    ['first', 'second', 'third'].forEach((query) => queueSend(slotId, { query }));
    mount();

    const rows = () => screen.getAllByTestId('queued-row');
    fireEvent.click(within(rows()[0]).getByRole('button', { name: 'Edit' }));
    expect(slot(slotId).composerRestore).toBe('first');
    expect(queueOf(slot(slotId)).map((q) => q.query)).toEqual(['second', 'third']);

    fireEvent.click(within(rows()[0]).getByRole('button', { name: 'Remove from queue' }));
    expect(queueOf(slot(slotId)).map((q) => q.query)).toEqual(['third']);

    fireEvent.click(screen.getByTestId('queue-clear'));
    expect(queueOf(slot(slotId))).toEqual([]);
    expect(screen.queryByTestId('queue-list')).toBeNull();
  });

  it('offers no Edit while the box holds a draft, so the two never mix', () => {
    const slotId = streamingChat();
    queueSend(slotId, { query: 'waiting' });
    useChatStore.getState().updateSlot(slotId, { composerHasText: true });
    mount();

    const edit = within(screen.getByTestId('queued-row')).getByRole('button', {
      name: "Send or clear the message you're typing to edit this one",
    }) as HTMLButtonElement;
    expect(edit.disabled).toBe(true);
  });

  it('shows a refused question with its reason and Retry', () => {
    const slotId = streamingChat();
    useChatStore.getState().updateSlot(slotId, {
      queue: [{ query: 'refused', clientMessageId: 'c-1', queuedAt: 1, failed: 'PipesHub is busy right now.' }],
    });
    mount();

    const row = screen.getByTestId('queued-row');
    expect(within(row).getByRole('alert').textContent).toBe('PipesHub is busy right now.');
    fireEvent.click(within(row).getByRole('button', { name: 'Retry' }));
    expect(queueOf(slot(slotId))[0]).not.toHaveProperty('failed');
  });
});
