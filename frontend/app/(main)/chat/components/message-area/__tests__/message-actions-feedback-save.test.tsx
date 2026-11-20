/** Like and Dislike show as pressed only once the rating is saved: a failed save leaves the button as it was. */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({
  submitFeedback: vi.fn(),
  submitAgentFeedback: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  slot: { convId: 'conv-1', threadAgentId: undefined as string | undefined, isOwner: true },
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
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
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));
vi.mock('../../../api', () => ({
  ChatApi: { submitFeedback: h.submitFeedback, submitAgentFeedback: h.submitAgentFeedback },
}));
vi.mock('../../../store', () => {
  const state = () => ({ activeSlotId: 's1', slots: { s1: h.slot } });
  const useChatStore = Object.assign((selector: (s: ReturnType<typeof state>) => unknown) => selector(state()), {
    getState: state,
  });
  return { useChatStore };
});

import { MessageActions } from '../message-actions';

// The chips sit in a Radix popover, which measures its anchor.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const renderActions = () =>
  render(
    <Theme>
      <MessageActions content="Q3 sales were 4M." messageId="msg-1" allowFeedback />
    </Theme>,
  );

const rate = async (button: 'chat.like' | 'chat.dislike', chip: string) => {
  fireEvent.click(screen.getByLabelText(button));
  fireEvent.click(await screen.findByText(chip));
};

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  h.slot.threadAgentId = undefined;
});

describe('a rating that could not be saved', () => {
  it('leaves Like un-pressed and says so', async () => {
    h.submitFeedback.mockRejectedValue(Object.assign(new Error('Conversation not found'), { statusCode: 404 }));
    renderActions();
    await rate('chat.like', 'chat.feedbackCategoryExcellentAnswer');
    await waitFor(() => expect(h.toastError).toHaveBeenCalledWith('chat.feedbackError'));
    expect(screen.getByLabelText('chat.like').getAttribute('aria-pressed')).toBe('false');
    expect(h.toastSuccess).not.toHaveBeenCalled();
  });

  it('leaves Dislike un-pressed, and an earlier Like still pressed', async () => {
    h.submitFeedback.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('down'));
    renderActions();
    await rate('chat.like', 'chat.feedbackCategoryExcellentAnswer');
    await waitFor(() => expect(screen.getByLabelText('chat.like').getAttribute('aria-pressed')).toBe('true'));
    await rate('chat.dislike', 'chat.feedbackCategoryIncorrectInfo');
    await waitFor(() => expect(h.toastError).toHaveBeenCalledWith('chat.feedbackError'));
    expect(screen.getByLabelText('chat.dislike').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByLabelText('chat.like').getAttribute('aria-pressed')).toBe('true');
  });
});

describe('a rating that was saved', () => {
  it('presses the button, in an agent thread too, through the agent feedback call', async () => {
    h.slot.threadAgentId = 'ops-quokka';
    h.submitAgentFeedback.mockResolvedValue(undefined);
    renderActions();
    await rate('chat.like', 'chat.feedbackCategoryExcellentAnswer');
    await waitFor(() => expect(screen.getByLabelText('chat.like').getAttribute('aria-pressed')).toBe('true'));
    expect(h.submitAgentFeedback).toHaveBeenCalledWith('ops-quokka', 'conv-1', 'msg-1', { isHelpful: true, categories: ['excellent_answer'] });
    expect(h.submitFeedback).not.toHaveBeenCalled();
  });
});
