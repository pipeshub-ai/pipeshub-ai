/**
 * QA6-F5: the web app sends the assistant only modes its routes accept. It used
 * to restore a query mode `image` from a conversation that stored one and send
 * it on the next regenerate; the server now refuses that value everywhere and
 * serves such a conversation in the default mode, and the page maps nothing to it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

installMemoryStorage('localStorage');
installMemoryStorage('sessionStorage');

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useChatStore, ASSISTANT_CTX } = await import('../store');
const { buildStreamRequestModeFields } = await import('../types');
const { applyConversationModelInfoToStore } = await import('../utils/apply-conversation-model-info');
const { QUERY_MODES } = await import('../constants');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');

/** `UNIVERSAL_STREAM_CHAT_MODES` of the server's `assistantChatModeSchema`. */
const ACCEPTED = ['agent', 'internal_search', 'web_search'];
const initial = useChatStore.getState().settings;

const sent = (): string => buildStreamRequestModeFields(useChatStore.getState().settings, false).chatMode;

beforeEach(() => {
  // Restoring a conversation refreshes the model list; nothing here leaves the process.
  fakeApi({});
  useChatStore.setState({ settings: { ...initial } });
});

describe('QA6-F5: the assistant chat mode the page sends', () => {
  it('F5-4: every query mode the page offers maps to a mode the routes accept', () => {
    for (const mode of QUERY_MODES) {
      useChatStore.getState().setQueryMode(mode.id);
      expect(ACCEPTED, mode.id).toContain(sent());
    }
    expect(QUERY_MODES.map((m) => m.id).sort()).toEqual(['agent', 'chat', 'web-search']);
  });

  it.each(['image', 'analysis', 'anything-else'])('F5-4: a conversation that stored %s restores to a mode the routes accept', (stored) => {
    useChatStore.getState().setQueryMode('web-search');
    applyConversationModelInfoToStore({ modelKey: 'm', modelName: 'n', chatMode: stored }, ASSISTANT_CTX);
    expect(useChatStore.getState().settings.queryMode).toBe('chat');
    expect(sent()).toBe('internal_search');
  });

  it('F5-4: the modes the server serves restore as themselves', () => {
    for (const [stored, queryMode, wire] of [
      ['internal_search', 'chat', 'internal_search'],
      ['web_search', 'web-search', 'web_search'],
      ['agent', 'agent', 'agent'],
    ] as const) {
      applyConversationModelInfoToStore({ modelKey: 'm', modelName: 'n', chatMode: stored }, ASSISTANT_CTX);
      expect(useChatStore.getState().settings.queryMode, stored).toBe(queryMode);
      expect(sent(), stored).toBe(wire);
    }
  });
});
