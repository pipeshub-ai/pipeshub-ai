/** Read aloud met with a refusal only an admin can lift says why, in the server's words; any other failure stays the generic line. */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({
  onError: null as null | ((error: string, refusal?: string) => void),
  toastError: vi.fn(),
  slot: { convId: 'conv-1', threadAgentId: undefined, isOwner: true },
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-chat-speech-synthesis', () => ({
  useChatSpeechSynthesis: (options: { onError?: (error: string, refusal?: string) => void }) => {
    h.onError = options.onError ?? null;
    return { isSupported: true, isSpeaking: false, speak: vi.fn(), stop: vi.fn() };
  },
}));
// The last answer's actions also take the /copy and /feedback commands.
vi.mock('@/lib/store/command-store', () => ({
  useCommandStore: { getState: () => ({ dispatch: vi.fn(), register: vi.fn(), unregister: vi.fn() }) },
}));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: h.toastError } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));
vi.mock('../../../api', () => ({ ChatApi: {} }));
vi.mock('../../../store', () => {
  // The store always holds its settings; the answer's actions read the models the app knows.
  const state = () => ({ activeSlotId: 's1', slots: { s1: h.slot }, bumpConversationsVersion: vi.fn(), settings: { availableModels: [] } });
  const useChatStore = Object.assign((selector: (s: ReturnType<typeof state>) => unknown) => selector(state()), { getState: state });
  return { useChatStore };
});

import { MessageActions } from '../message-actions';

beforeEach(() => {
  h.toastError.mockReset();
  render(
    <Theme>
      <MessageActions content="The roadmap has 3 themes." messageId="msg-2" isLastMessage />
    </Theme>,
  );
});
afterEach(cleanup);

describe('read aloud refused', () => {
  it("says why, in the server's words, for a refusal only an admin can lift", () => {
    h.onError?.('Request failed', 'This request is not allowed right now. Ask your admin.');
    expect(h.toastError.mock.calls.map(([title]) => title)).toEqual(['This request is not allowed right now. Ask your admin.']);
  });

  it('keeps the generic line for any other failure', () => {
    h.onError?.('speech-synthesis-failed');
    expect(h.toastError.mock.calls.map(([title]) => title)).toEqual(['chat.ttsFailed']);
  });

  it('keeps saying when the browser cannot read aloud', () => {
    h.onError?.('not-supported');
    expect(h.toastError.mock.calls.map(([title]) => title)).toEqual(['chat.ttsNotSupported']);
  });
});
