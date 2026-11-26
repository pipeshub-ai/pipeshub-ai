/**
 * SS3 UI-2, UI-3, UI-4: the "Continue privately" confirmation says what is
 * copied, sends one request per confirmation, and a copied stub reads as a
 * translated placeholder.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ConversationMessage } from '../types';

const forkConversation = vi.fn();
vi.mock('../api', () => ({
  ChatApi: { continuePrivately: (...args: unknown[]) => forkConversation(...args) },
}));
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const { ContinuePrivately, forkMode } = await import('../components/continue-privately');
const { useCommandStore } = await import('@/lib/store/command-store');
const { BUILTIN_BUS } = await import('../builtin-commands');
const { loadHistoricalMessages } = await import('../runtime');

function renderButton(isOwner = false) {
  const onContinued = vi.fn();
  render(
    <Theme>
      <ContinuePrivately conversationId="conv-1" isOwner={isOwner} onContinued={onContinued} />
    </Theme>,
  );
  return onContinued;
}

const open = () => fireEvent.click(screen.getByRole('button', { name: 'Continue privately' }));

afterEach(() => cleanup());
beforeEach(() => vi.clearAllMocks());

describe('ContinuePrivately (SS3)', () => {
  it('UI-2: the confirmation says what is copied and what is not', () => {
    renderButton(false);
    open();
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('Your own messages and the answers to them');
    expect(dialog.textContent).toContain('The sources and files of those answers that you can open');
    expect(dialog.textContent).toContain("Other people's messages: each becomes a placeholder");
    expect(dialog.textContent).toContain('Tool results, reasoning and attachments');
    expect(dialog.textContent).not.toContain("workspace");
  });

  it("UI-2: the owner's copy also takes the workspace files", () => {
    renderButton(true);
    open();
    expect(screen.getByRole('dialog').textContent).toContain("The files in this conversation's workspace");
  });

  it('UI-3: confirming sends one request with one key, then opens the copy', async () => {
    let resolve: (value: { conversationId: string; replayed: boolean }) => void = () => undefined;
    forkConversation.mockImplementation(() => new Promise((r) => (resolve = r)));
    const onContinued = renderButton();
    open();
    const confirm = screen.getAllByRole('button', { name: 'Continue privately' }).at(-1)!;
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(forkConversation).toHaveBeenCalledTimes(1);
    const [conversationId, key] = forkConversation.mock.calls[0] as [string, string];
    expect(conversationId).toBe('conv-1');
    expect(key).toMatch(/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/);
    resolve({ conversationId: 'conv-copy', replayed: false });
    await waitFor(() => expect(onContinued).toHaveBeenCalledWith('conv-copy'));
  });

  it('UI-3: a refusal is said and the dialog stays; trying again keeps the same key', async () => {
    forkConversation.mockRejectedValueOnce(new Error('409'));
    forkConversation.mockResolvedValueOnce({ conversationId: 'conv-copy', replayed: true });
    const onContinued = renderButton();
    open();
    fireEvent.click(screen.getAllByRole('button', { name: 'Continue privately' }).at(-1)!);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(onContinued).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole('button', { name: 'Continue privately' }).at(-1)!);
    await waitFor(() => expect(onContinued).toHaveBeenCalledWith('conv-copy'));
    const keys = forkConversation.mock.calls.map((call) => call[1]);
    expect(new Set(keys).size).toBe(1);
  });
});

describe('/fork on the owner’s own chat (QAFIX-4)', () => {
  it('is available on the owner’s own chats, agent or not, and on any shared non-agent chat', () => {
    const base = { served: true, conversationId: 'conv-1', isOwner: true, isShared: false, agentThread: false };
    expect(forkMode(base)).toBe('own');
    expect(forkMode({ ...base, agentThread: true })).toBe('own');
    expect(forkMode({ ...base, isShared: true })).toBe('shared');
    expect(forkMode({ ...base, isOwner: false, isShared: true })).toBe('shared');
    expect(forkMode({ ...base, isOwner: false })).toBeNull();
    expect(forkMode({ ...base, isOwner: null })).toBeNull();
    expect(forkMode({ ...base, served: false })).toBeNull();
    expect(forkMode({ ...base, conversationId: null })).toBeNull();
  });

  it('shows no header button; /fork opens a fork confirmation that copies into an agent chat by its agent', async () => {
    forkConversation.mockResolvedValueOnce({ conversationId: 'conv-copy', replayed: false });
    const onContinued = vi.fn();
    render(
      <Theme>
        <ContinuePrivately conversationId="conv-1" isOwner mode="own" agentKey="agent-1" onContinued={onContinued} />
      </Theme>,
    );
    expect(screen.queryByRole('button')).toBeNull();
    const handler = useCommandStore.getState().handlers[BUILTIN_BUS.continuePrivately];
    expect(handler).toBeTypeOf('function');
    act(() => handler?.());

    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('Fork this chat');
    expect(dialog.textContent).toContain("The files in this conversation's workspace");
    expect(dialog.textContent).not.toContain("Other people's messages");
    fireEvent.click(screen.getByRole('button', { name: 'Fork' }));
    await waitFor(() => expect(onContinued).toHaveBeenCalledWith('conv-copy'));
    expect(forkConversation.mock.calls[0]).toEqual(['conv-1', expect.any(String), 'agent-1']);
  });
});

describe('a copied stub (SS3 UI-4)', () => {
  const message = (overrides: Partial<ConversationMessage>): ConversationMessage =>
    ({
      _id: 'm',
      messageType: 'user_query',
      content: '[A turn by another participant in this shared conversation is not shown to you.]',
      contentFormat: 'MARKDOWN',
      citations: [],
      followUpQuestions: [],
      feedback: [],
      createdAt: '2026-10-04T00:00:00.000Z',
      updatedAt: '2026-10-04T00:00:00.000Z',
      foreignTurn: true,
      ...overrides,
    }) as ConversationMessage;

  it('reads as the translated placeholder, question and answer alike', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q' }),
      message({ _id: 'a', messageType: 'bot_response' }),
      message({ _id: 'q2', foreignTurn: false, content: 'My own question' }),
    ]);
    const texts = messages.map((m) => (m.content as readonly { text?: string }[])[0]?.text);
    expect(texts).toEqual([
      'A message from another participant is not part of your private copy.',
      'A message from another participant is not part of your private copy.',
      'My own question',
    ]);
  });
});
