import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { chatSlots } from '@/config';

const captured = vi.hoisted(() => ({ props: null as null | Record<string, unknown> }));

vi.mock('@assistant-ui/react', () => ({
  useThreadRuntime: () => ({ append: vi.fn() }),
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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
