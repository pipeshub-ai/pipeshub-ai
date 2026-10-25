import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

const captured = vi.hoisted(() => ({
  props: null as null | Record<string, unknown>,
  agentId: null as string | null,
}));

vi.mock('@assistant-ui/react', () => ({
  useAui: () => ({ thread: () => ({ append: vi.fn() }) }),
}));

vi.mock('../../chat-input', () => ({
  ChatInput: (props: Record<string, unknown>) => {
    captured.props = props;
    return <textarea aria-label="Message composer" />;
  },
}));

vi.mock('@/chat/hooks/use-effective-agent-id', () => ({
  useEffectiveAgentId: () => captured.agentId,
}));

vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: () => Promise.resolve(),
}));

import { ChatInputWrapper } from '../chat-input-wrapper';

beforeEach(() => {
  captured.props = null;
  captured.agentId = null;
});

afterEach(() => cleanup());

describe('ChatInputWrapper', () => {
  it('keeps unsent text in the main chat composer', () => {
    render(<ChatInputWrapper />);

    expect(captured.props?.persistDraft).toBe(true);
  });

  it('keeps unsent text in an agent chat composer too, and tells it which agent', () => {
    captured.agentId = 'agent-7';

    render(<ChatInputWrapper />);

    expect(captured.props?.persistDraft).toBe(true);
    expect(captured.props?.agentId).toBe('agent-7');
    expect(captured.props?.isAgentChat).toBe(true);
  });
});
