import { BadRequestError } from '../../../libs/errors/http.errors';
import { getConversationRepository } from '../../../libs/repositories/conversation';
import { oidOf } from '../../../libs/repositories/pg-util';

export const TEMPORARY_CHAT_TTL_MS = 24 * 60 * 60 * 1000;
export const TEMPORARY_CHAT_TITLE = 'Temporary chat';
export const TEMPORARY_CHAT_ACTION_ERROR =
  'Temporary chats cannot be shared, pinned, archived, forked, renamed, soft-deleted, or added to a project';

export function isTemporarySession(session: {
  isTemporary?: boolean;
}): boolean {
  return session.isTemporary === true;
}

export function assertNotTemporary(session: { isTemporary?: boolean }): void {
  if (isTemporarySession(session)) {
    throw new BadRequestError(TEMPORARY_CHAT_ACTION_ERROR);
  }
}

export function temporaryExpiry(now = Date.now()): Date {
  return new Date(now + TEMPORARY_CHAT_TTL_MS);
}

/** A follow-up or regenerate carries no `temporary` field, so the stored chat is what says it is one. */
export async function isTemporaryConversation(
  owner: { orgId: string; actorId: string },
  conversationId: string,
): Promise<boolean> {
  // No stored chat has such an id, so there is nothing to read; the route refuses it itself.
  if (oidOf(conversationId) === undefined) return false;
  const found = await getConversationRepository().findOwned(
    { kind: 'chat' },
    conversationId,
    { orgId: owner.orgId, userId: owner.actorId },
  );
  return found !== null && isTemporarySession(found);
}
