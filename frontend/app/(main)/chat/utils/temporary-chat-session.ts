/**
 * Tab-scoped handle for a temporary chat. sessionStorage survives refresh
 * and is cleared when the tab closes, matching ChatGPT / Claude / Gemini:
 * stay in the thread on F5, do not put it in history.
 */
const KEY = 'pipeshub-temporary-chat';

export type TemporaryChatSession = {
  conversationId: string | null;
};

function storage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function readTemporaryChatSession(): TemporaryChatSession | null {
  const raw = storage()?.getItem(KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as TemporaryChatSession;
    if (!parsed || typeof parsed !== 'object') return null;
    const conversationId =
      typeof parsed.conversationId === 'string' && parsed.conversationId
        ? parsed.conversationId
        : null;
    return { conversationId };
  } catch {
    return null;
  }
}

export function writeTemporaryChatSession(conversationId: string | null): void {
  storage()?.setItem(KEY, JSON.stringify({ conversationId }));
}

export function clearTemporaryChatSession(): void {
  storage()?.removeItem(KEY);
}
