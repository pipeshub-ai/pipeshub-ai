import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { chatSlots } from '@/config';

const captured = vi.hoisted(() => ({ props: null as null | Record<string, unknown> }));
const runtime = vi.hoisted(() => ({ append: vi.fn() }));

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

import { ChatInputWrapper } from '../chat-input-wrapper';
import { useChatStore } from '@/chat/store';

const initialChat = useChatStore.getState();

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  runtime.append.mockReset();
  useChatStore.setState(initialChat, true);
});

/** The composer's `onSend`, with an extension that takes every send and answers `result`. */
function sendTakenAs(result: boolean | 'keep'): (message: string) => unknown {
  vi.spyOn(chatSlots, 'useComposerSend').mockReturnValue({
    accessories: null,
    intercept: () => Promise.resolve(result),
    beforeAppend: () => undefined,
    messageCustom: () => ({}),
    afterAppend: () => undefined,
  });
  render(<ChatInputWrapper />);
  return captured.props?.onSend as (message: string) => unknown;
}

describe('ChatInputWrapper: a send the extension takes', () => {
  it("keeps the draft when the extension took the send but sent nothing ('keep')", async () => {
    await expect(sendTakenAs('keep')('hi')).resolves.toBe(false);
  });

  it('lets the composer clear the draft when the extension sent it', async () => {
    await expect(sendTakenAs(true)('hi')).resolves.toBeUndefined();
  });
});

describe('ChatInputWrapper: the chat as it is once the extension is done (F5)', () => {
  /** A chat whose run state the extension's wait (a confirm dialog) changes from `before` to `after`. */
  function sendAcrossWait(before: boolean, after: boolean): (message: string) => unknown {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true, isStreaming: before });
    store.setActiveSlot(slotId);
    vi.spyOn(chatSlots, 'useComposerSend').mockReturnValue({
      accessories: null,
      intercept: () => {
        useChatStore.getState().updateSlot(slotId, { isStreaming: after });
        return Promise.resolve(false);
      },
      beforeAppend: () => undefined,
      messageCustom: () => ({}),
      afterAppend: () => undefined,
    });
    render(<ChatInputWrapper />);
    return captured.props?.onSend as (message: string) => unknown;
  }

  it('sends once the answer that was running when Enter was pressed has finished', async () => {
    await expect(sendAcrossWait(true, false)('hi')).resolves.toBeUndefined();
    expect(runtime.append).toHaveBeenCalledOnce();
  });

  it('refuses a send while a run started meanwhile, and keeps the draft', async () => {
    await expect(sendAcrossWait(false, true)('hi')).resolves.toBe(false);
    expect(runtime.append).not.toHaveBeenCalled();
  });
});
