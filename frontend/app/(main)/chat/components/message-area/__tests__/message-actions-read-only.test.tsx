import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({
  slot: { convId: 'conv-1', threadAgentId: undefined as string | undefined, isOwner: true as boolean | null },
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} />,
}));
vi.mock('@/lib/hooks/use-chat-speech-synthesis', () => ({
  useChatSpeechSynthesis: () => ({ isSupported: false, isSpeaking: false, speak: vi.fn(), stop: vi.fn() }),
}));
vi.mock('@/lib/store/command-store', () => ({ useCommandStore: { getState: () => ({ dispatch: vi.fn() }) } }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));
vi.mock('../../../api', () => ({ ChatApi: {} }));
vi.mock('../../../store', () => {
  const state = () => ({
    activeSlotId: 's1',
    slots: { s1: h.slot },
    bumpConversationsVersion: vi.fn(),
  });
  const useChatStore = Object.assign((selector: (s: ReturnType<typeof state>) => unknown) => selector(state()), {
    getState: state,
  });
  return { useChatStore };
});

import { MessageActions } from '../message-actions';

const renderActions = () =>
  render(
    <Theme>
      <MessageActions content="The roadmap has 3 themes." messageId="msg-2" isLastMessage />
    </Theme>,
  );

const hasFeedbackButtons = () =>
  document.querySelector('[data-icon="thumb_up_off_alt"]') !== null &&
  document.querySelector('[data-icon="thumb_down_off_alt"]') !== null;

afterEach(cleanup);

beforeEach(() => {
  h.slot.convId = 'conv-1';
  h.slot.threadAgentId = undefined;
  h.slot.isOwner = true;
});

describe('message actions on a shared agent chat (read-only for recipients)', () => {
  it('offers the owner rating and regenerate on their agent chat', () => {
    h.slot.threadAgentId = 'agent-7';
    renderActions();
    expect(hasFeedbackButtons()).toBe(true);
    expect(screen.getByLabelText('chat.regenerate')).toBeTruthy();
  });

  it('offers neither rating nor regenerate to a recipient of an agent chat', () => {
    h.slot.threadAgentId = 'agent-7';
    h.slot.isOwner = false;
    renderActions();
    expect(hasFeedbackButtons()).toBe(false);
    expect(screen.queryByLabelText('chat.regenerate')).toBeNull();
    expect(screen.queryByLabelText('chat.fork')).toBeNull();
  });

  it('still lets a recipient copy the answer', () => {
    h.slot.threadAgentId = 'agent-7';
    h.slot.isOwner = false;
    renderActions();
    expect(document.querySelector('[data-icon="content_copy"]')).not.toBeNull();
  });

  it('keeps rating and regenerate while ownership is still loading', () => {
    h.slot.threadAgentId = 'agent-7';
    h.slot.isOwner = null;
    renderActions();
    expect(hasFeedbackButtons()).toBe(true);
    expect(screen.getByLabelText('chat.regenerate')).toBeTruthy();
  });

  it('keeps rating for a recipient of a plain chat, which the server allows, but hides regenerate, which it refuses', () => {
    h.slot.isOwner = false;
    renderActions();
    expect(hasFeedbackButtons()).toBe(true);
    expect(screen.queryByLabelText('chat.regenerate')).toBeNull();
  });
});

describe('message actions footer names the search the answer used', () => {
  const renderWith = (parts?: { type: 'tool_call'; toolName: string }[]) =>
    render(
      <Theme>
        <MessageActions
          content="Answer"
          messageId="msg-3"
          modelInfo={{ chatMode: 'agent' } as never}
          parts={parts as never}
        />
      </Theme>,
    );

  it('shows internal search when the answer searched the knowledge base', () => {
    renderWith([{ type: 'tool_call', toolName: 'retrieval.search_internal_knowledge' }]);
    expect(screen.getByText('chat.answerMode.internalSearch')).toBeTruthy();
  });

  it('shows web search when the answer only searched the web', () => {
    renderWith([{ type: 'tool_call', toolName: 'web_search' }]);
    expect(screen.getByText('chat.answerMode.webSearch')).toBeTruthy();
  });

  it('keeps Agent when no search tool ran', () => {
    renderWith([]);
    expect(screen.getByText('Agent')).toBeTruthy();
  });
});
