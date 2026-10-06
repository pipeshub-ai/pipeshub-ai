import { expect, type Page } from '@playwright/test';
import { fake, stackState, waitFor, type Actor, type NodeApi } from './stack';
import { composer } from './chat-ui';

export { composer };

/** Prefix of every chat a journey creates, so a leftover is recognisable. */
export const CHAT_PREFIX = 'e2e-collab-';

/** `e2e-collab-<journey> <text>`: the first question becomes the chat's title. */
export const chatTitle = (journey: string, text = 'first question') => `${CHAT_PREFIX}${journey} ${text}`;

/** Shares `conversationId` with a user (or a team) through the real collaborators route. */
export async function shareChat(
  api: NodeApi,
  conversationId: string,
  target: { userId: string; level: 'read' | 'write' } | { teamId: string; level: 'read' | 'write' },
  note?: string,
): Promise<void> {
  const principal = 'teamId' in target ? { principalType: 'team', principalId: target.teamId } : { principalType: 'user', principalId: target.userId };
  const r = await api.call('PUT', `/api/v1/conversations/${conversationId}/collaborators`, {
    collaborators: [{ ...principal, accessLevel: target.level }],
    ...(note ? { note } : {}),
  });
  if (r.status !== 200) throw new Error(`share ${conversationId}: ${r.status} ${JSON.stringify(r.body)}`);
}

/** The chat's current `rev` as the feed route reports it (the counter every change to the chat bumps). */
export async function feedRev(api: NodeApi, conversationId: string): Promise<number> {
  const r = await api.call('GET', `/api/v1/conversations/${conversationId}/feed?afterSeq=-1`);
  if (r.status !== 200) throw new Error(`feed ${conversationId}: ${r.status}`);
  return r.body.rev as number;
}

/**
 * Resolves when the page's own feed poll has seen `rev` (or later). Waits on the poll the page makes, never on a timer, so
 * "the other tab has caught up" is a fact about the page and not a guess about time.
 */
export async function waitForFeedRev(page: Page, rev: number, timeout = 25_000): Promise<void> {
  const seen = (r: import('@playwright/test').Response) => r.url().includes('/feed') && r.request().method() === 'GET';
  const deadline = Date.now() + timeout;
  for (;;) {
    const left = deadline - Date.now();
    if (left <= 0) throw new Error(`the page never polled a feed with rev >= ${rev}`);
    const res = await page.waitForResponse(seen, { timeout: left });
    if (res.status() !== 200) continue;
    const body = (await res.json().catch(() => null)) as { rev?: number } | null;
    if (typeof body?.rev === 'number' && body.rev >= rev) return;
  }
}

/** Turns collaboration flags on or off and waits until the API's effective-flags answer (what the UI reads) agrees. */
export async function setFlags(flags: Record<string, boolean>, as?: Actor): Promise<void> {
  const viewer = as ?? stackState().roster.owner;
  // Node's gates share one 10 s flag cache, and only the collaborative-chats write waits until Node enforces it
  // (the effective-flags route below reads the store directly). Written last, its wait covers the other keys too.
  const last = (key: string): number => (key === 'ENABLE_COLLABORATIVE_CHATS' ? 1 : 0);
  const ordered = Object.entries(flags).sort(([x], [y]) => last(x) - last(y));
  for (const [key, enabled] of ordered) await fake.setFlag(enabled, key);
  await waitFor(
    `flags ${JSON.stringify(flags)} to be effective`,
    async () => {
      const res = await fetch(`${stackState().nodeUrl}/api/v1/configurationManager/platform/feature-flags/effective`, {
        headers: { authorization: `Bearer ${viewer.token}` },
      });
      const body = (await res.json()) as { featureFlags?: Record<string, boolean> };
      return Object.entries(flags).every(([k, v]) => (body.featureFlags?.[k] ?? false) === v);
    },
    40_000,
    500,
  );
}

export const COLLAB_FLAGS = ['ENABLE_COLLABORATIVE_CHATS', 'ENABLE_CHAT_MENTIONS', 'ENABLE_CHAT_AGENT_BUILDER'] as const;

/** The composer of a chat the page has open, whichever input it uses. */
export async function expectComposerVisible(page: Page): Promise<void> {
  await expect(composer(page)).toBeVisible();
}
