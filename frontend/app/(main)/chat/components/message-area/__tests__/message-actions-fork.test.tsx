import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({
  push: vi.fn(),
  bump: vi.fn(),
  forkConversation: vi.fn(),
  forkAgentConversation: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  slot: { convId: 'conv-1', threadAgentId: undefined as string | undefined, projectId: undefined as string | undefined, isOwner: true as boolean | null },
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: h.push }) }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-chat-speech-synthesis', () => ({
  useChatSpeechSynthesis: () => ({ isSupported: false, isSpeaking: false, speak: vi.fn(), stop: vi.fn() }),
}));
vi.mock('@/lib/store/command-store', () => ({ useCommandStore: { getState: () => ({ dispatch: vi.fn() }) } }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: h.toastSuccess, error: h.toastError } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: { forkAgentConversation: h.forkAgentConversation } }));
vi.mock('../../../api', () => ({
  ChatApi: { forkConversation: h.forkConversation },
}));
vi.mock('../../../store', () => {
  const state = () => ({
    activeSlotId: 's1',
    slots: { s1: h.slot },
    bumpConversationsVersion: h.bump,
  });
  const useChatStore = Object.assign((selector: (s: ReturnType<typeof state>) => unknown) => selector(state()), {
    getState: state,
  });
  return { useChatStore };
});

import { MessageActions } from '../message-actions';

const renderActions = (messageId?: string, isLastMessage = false) =>
  render(
    <Theme>
      <MessageActions content="Q3 sales were 4M." messageId={messageId} isLastMessage={isLastMessage} />
    </Theme>,
  );

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  h.slot.convId = 'conv-1';
  h.slot.threadAgentId = undefined;
  h.slot.projectId = undefined;
  h.slot.isOwner = true;
});

describe('fork button on a message', () => {
  it('is not offered before the message has an id to fork at', () => {
    renderActions(undefined);
    expect(screen.queryByLabelText('chat.forkFromHere')).toBeNull();
  });

  it('is not offered on a chat that was shared with the user', () => {
    h.slot.isOwner = false;
    renderActions('msg-2');
    expect(screen.queryByLabelText('chat.forkFromHere')).toBeNull();
  });

  it('is still offered while ownership is not known yet', () => {
    h.slot.isOwner = null;
    renderActions('msg-2');
    expect(screen.getByLabelText('chat.forkFromHere')).toBeTruthy();
  });

  it('says "fork from here" on a message in the middle of the chat', () => {
    renderActions('msg-2', false);
    expect(screen.getByLabelText('chat.forkFromHere')).toBeTruthy();
    expect(screen.queryByLabelText('chat.fork')).toBeNull();
  });

  it('says "fork chat" on the last message', () => {
    renderActions('msg-9', true);
    expect(screen.getByLabelText('chat.fork')).toBeTruthy();
    expect(screen.queryByLabelText('chat.forkFromHere')).toBeNull();
  });

  it('forks an assistant chat at that message and opens the new chat', async () => {
    h.forkConversation.mockResolvedValue({ id: 'fork-9', title: 'Fork of A', projectId: undefined, forkedFrom: {}, messageCount: 2 });
    renderActions('msg-2');

    fireEvent.click(screen.getByLabelText('chat.forkFromHere'));

    await waitFor(() => expect(h.push).toHaveBeenCalledWith('/chat/?conversationId=fork-9'));
    expect(h.forkConversation).toHaveBeenCalledWith('conv-1', 'msg-2');
    expect(h.bump).toHaveBeenCalled();
    expect(h.toastSuccess).toHaveBeenCalledWith('chat.toasts.forkSuccess');
  });

  it('forks an agent chat through the agent route and stays on that agent', async () => {
    h.slot.threadAgentId = 'agent-7';
    h.forkAgentConversation.mockResolvedValue({ id: 'fork-3', title: 'Fork of A', forkedFrom: {}, messageCount: 4 });
    renderActions('msg-4');

    fireEvent.click(screen.getByLabelText('chat.forkFromHere'));

    await waitFor(() => expect(h.push).toHaveBeenCalledWith('/chat/?agentId=agent-7&conversationId=fork-3'));
    expect(h.forkAgentConversation).toHaveBeenCalledWith('agent-7', 'conv-1', 'msg-4');
    expect(h.forkConversation).not.toHaveBeenCalled();
  });

  it('opens the fork inside the project the server kept it in', async () => {
    h.forkConversation.mockResolvedValue({ id: 'fork-5', title: 'Fork of A', projectId: 'proj-1', forkedFrom: {}, messageCount: 2 });
    h.slot.projectId = 'proj-old';
    renderActions('msg-2');

    fireEvent.click(screen.getByLabelText('chat.forkFromHere'));

    await waitFor(() => expect(h.push).toHaveBeenCalledWith('/chat/?projectId=proj-1&conversationId=fork-5'));
  });

  it('does not bring back a project the server dropped from the fork', async () => {
    h.forkConversation.mockResolvedValue({ id: 'fork-6', title: 'Fork of A', forkedFrom: {}, messageCount: 2 });
    h.slot.projectId = 'proj-lost';
    renderActions('msg-2');

    fireEvent.click(screen.getByLabelText('chat.forkFromHere'));

    await waitFor(() => expect(h.push).toHaveBeenCalledWith('/chat/?conversationId=fork-6'));
  });

  it('tells the user and stays on the current chat when the fork fails', async () => {
    h.slot.projectId = 'proj-old';
    h.forkConversation.mockRejectedValue(new Error('nope'));
    renderActions('msg-2');

    fireEvent.click(screen.getByLabelText('chat.forkFromHere'));

    await waitFor(() => expect(h.toastError).toHaveBeenCalledWith('chat.toasts.forkError'));
    expect(h.push).not.toHaveBeenCalled();
    expect(h.bump).not.toHaveBeenCalled();
  });
});
