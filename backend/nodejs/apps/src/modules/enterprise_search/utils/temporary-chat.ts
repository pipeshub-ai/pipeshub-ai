import { BadRequestError } from '../../../libs/errors/http.errors';

export const TEMPORARY_CHAT_TTL_MS = 24 * 60 * 60 * 1000;
export const TEMPORARY_CHAT_TITLE = 'Temporary chat';
export const TEMPORARY_CHAT_ACTION_ERROR =
  'Temporary chats cannot be shared, pinned, archived, forked, renamed, or added to a project';

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
