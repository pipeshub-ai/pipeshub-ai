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

const search = vi.hoisted(() => vi.fn());
vi.mock('@/chat/api', () => ({ ChatApi: { search: (...args: unknown[]) => search(...args) } }));

import { ChatInputWrapper } from '../chat-input-wrapper';
import { useChatStore } from '@/chat/store';
import { ErrorType } from '@/lib/api/api-error';

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

describe('ChatInputWrapper search', () => {
  type Send = (message: string) => Promise<void>;
  const send = (q: string) => (captured.props?.onSend as Send)(q);

  beforeEach(() => {
    search.mockReset();
    useChatStore.getState().reset();
    useChatStore.getState().setMode('search');
  });

  it('drops a search that was closed while it ran', async () => {
    let answer: (v: unknown) => void = () => {};
    search.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    render(<ChatInputWrapper />);

    const pending = send('alpha');
    expect(useChatStore.getState().searchQuery).toBe('alpha');
    useChatStore.getState().clearSearchResults();
    answer({ searchId: 's1', searchResponse: { searchResults: [{ content: 'old' }] } });
    await pending;

    const state = useChatStore.getState();
    expect(state.searchResults).toEqual([]);
    expect(state.searchQuery).toBe('');
    expect(state.isSearching).toBe(false);
  });

  it("keeps the query and the server's reason when there is nothing to search", async () => {
    search.mockRejectedValue({
      type: ErrorType.NOT_FOUND,
      message: 'No documents are available for you to search yet.',
      details: { apiStatus: 'accessible_records_not_found' },
    });
    render(<ChatInputWrapper />);

    await send('alpha');

    const state = useChatStore.getState();
    expect(state.searchQuery).toBe('alpha');
    expect(state.searchError).toBeNull();
    expect(state.searchResponseMeta?.message).toBe('No documents are available for you to search yet.');
  });

  it('keeps the query when the search fails', async () => {
    search.mockRejectedValue({ type: ErrorType.SERVER_ERROR, message: 'Search is unavailable.' });
    render(<ChatInputWrapper />);

    await send('alpha');

    const state = useChatStore.getState();
    expect(state.searchQuery).toBe('alpha');
    expect(state.searchError).toBe('Search is unavailable.');
  });
});
