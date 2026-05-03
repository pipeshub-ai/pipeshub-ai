import { useChatStore } from './store';

/**
 * Build `/chat/` URLs with optional agent and conversation query params.
 * Uses trailing slash to match existing history.replaceState usage on the chat page.
 */
export function buildChatHref(options: {
  agentId?: string | null;
  conversationId?: string | null;
}): string {
  const q = new URLSearchParams();
  if (options.agentId) q.set('agentId', options.agentId);
  if (options.conversationId) q.set('conversationId', options.conversationId);
  const qs = q.toString();
  return qs ? `/chat/?${qs}` : '/chat/';
}

/**
 * New chat inside an agent: clear the visible slot (background streams keep running),
 * sync URL. `router.replace` + replaceState avoids a no-op when the URL is unchanged.
 */
export function openFreshAgentChat(agentId: string, router: { replace: (href: string) => void }): void {
  useChatStore.getState().clearActiveSlot();
  const href = buildChatHref({ agentId });
  if (typeof window !== 'undefined') {
    window.history.replaceState(null, '', href);
  }
  router.replace(href);
}
