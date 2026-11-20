/**
 * The chat page's last open chat stays active until the Collections / All records box has mounted. Text given back to
 * that chat (a cancelled queued question, a refused send) is the chat's: the box starts a new chat, so it starts empty
 * and leaves the text on the chat for when the reader goes back.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/knowledge-base',
}));
vi.mock('@/chat/streaming', () => ({ cancelStreamForSlot: vi.fn(), streamRegenerateForSlot: vi.fn() }));
vi.mock('@/lib/hooks/use-chat-speech-recognition', () => ({
  useChatSpeechRecognition: () => ({
    isListening: false,
    isSupported: true,
    transcript: '',
    interimTranscript: '',
    unavailableReason: null,
    toggle: vi.fn(),
    stop: vi.fn(),
    resetTranscript: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({ fetchModelsForContext: () => Promise.resolve() }));

import { ChatWidgetWrapper } from '../chat-widget-wrapper';
import { useChatStore } from '@/chat/store';

const initialChatState = useChatStore.getState();
const GIVEN_BACK = 'the question taken out of the queue';

class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  useChatStore.setState(initialChatState, true);
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const box = () => document.querySelector<HTMLTextAreaElement>('[data-testid="chat-composer"]')!;

/** The chat page left with a chat open whose box was given text back. */
function leaveChatWithTextGivenBack() {
  const store = useChatStore.getState();
  const slotId = store.createSlot('c-1');
  store.updateSlot(slotId, { isInitialized: true, composerRestore: GIVEN_BACK });
  store.setActiveSlot(slotId);
  return slotId;
}

describe("the Collections / All records box opened from a chat", () => {
  it.each([false, true])('starts empty and leaves the chat its text (all records: %s)', (isAllRecordsMode) => {
    const slotId = leaveChatWithTextGivenBack();

    render(<ChatWidgetWrapper currentTitle="" selectedKbId={null} isAllRecordsMode={isAllRecordsMode} />);

    expect(box().value).toBe('');
    expect(useChatStore.getState().slots[slotId].composerRestore).toBe(GIVEN_BACK);
  });
});
