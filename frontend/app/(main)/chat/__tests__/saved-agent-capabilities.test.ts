import { describe, it, expect, vi } from 'vitest';

// The store reads the universal capabilities when it is created, so they are saved before it is imported.
vi.hoisted(() => {
  const saved = JSON.stringify({ internalSearch: true, webSearch: true, deepSearch: true });
  window.localStorage.setItem('pipeshub-agent-capabilities', saved);
  window.localStorage.setItem('pipeshub-agent-capabilities:agent-1', saved);
});

import { useChatStore } from '../store';

describe('capabilities saved before the deep research toggle was removed', () => {
  it('are loaded without deep research for the main chat', () => {
    expect(useChatStore.getState().settings.agentCapabilities).toStrictEqual({
      internalSearch: true,
      webSearch: true,
    });
  });

  it('are loaded without deep research for an agent, and saved back without it', () => {
    useChatStore.getState().setScopedAgentCapabilities('agent-1', { webSearch: false });

    expect(useChatStore.getState().scopedAgentCapabilities['agent-1']).toStrictEqual({
      internalSearch: true,
      webSearch: false,
    });
    const stored = JSON.parse(window.localStorage.getItem('pipeshub-agent-capabilities:agent-1') ?? '{}');
    expect(stored).toStrictEqual({ internalSearch: true, webSearch: false });
  });
});
