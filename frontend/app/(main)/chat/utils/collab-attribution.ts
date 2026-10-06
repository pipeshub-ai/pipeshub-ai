import type { MessageAuthor } from '../collaboration-types';
import type { ConversationAccess } from './conversation-access';
import { canRegenerate } from './conversation-access';

type Asker = { requestedBy?: MessageAuthor | null; author?: MessageAuthor | null };

/**
 * The API stores `requestedBy` as a user id; the feed and the detail response (flag on) also send the person as
 * `author`. Everything downstream compares `requestedBy.userId`, so a bare id becomes a
 * `MessageAuthor` here (with the row's `author` when it is the same person, else without a name).
 * Returns the same array when no row carries a bare id.
 */
export function withRequestedByViews<T extends { requestedBy?: MessageAuthor | null; author?: MessageAuthor | null }>(
  messages: T[],
): T[] {
  let changed = false;
  const out = messages.map((m) => {
    const raw: unknown = m.requestedBy;
    if (typeof raw !== 'string') return m;
    changed = true;
    const view: MessageAuthor = m.author?.userId === raw ? m.author : { userId: raw, displayName: null };
    return { ...m, requestedBy: view };
  });
  return changed ? out : messages;
}

/**
 * Who the answer was run for: the stamped `requestedBy`, else the question's author. `undefined`
 * when the row carries neither (solo chat, legacy row, optimistic row).
 */
export function askerOf(msg: Asker): MessageAuthor | null | undefined {
  return msg.requestedBy !== undefined ? msg.requestedBy : msg.author;
}

/**
 * Whether a turn shows who sent or asked it. A shared chat always does. Once nobody else is left in the chat
 * (everyone left or was removed), a turn by someone else still names them, so their questions do not read as
 * the viewer's own.
 */
export function attributionVisible(
  collabActive: boolean,
  collabEnabled: boolean,
  person: MessageAuthor | null | undefined,
  meUserId: string | null | undefined,
): person is MessageAuthor | null {
  if (person === undefined) return false;
  if (collabActive) return true;
  if (!collabEnabled) return false;
  return person === null || (Boolean(meUserId) && person.userId !== meUserId);
}

/**
 * Regenerate is offered to the asker alone (O-1/D8). A solo chat, or a flag-off build, keeps today's rule;
 * a collaborative row with no author information falls back to the owner.
 */
export function regenerateAllowed(
  access: Pick<ConversationAccess, 'canSend' | 'isOwner' | 'isCollaborative' | 'isReadOnly'>,
  collabEnabled: boolean,
  msg: Asker,
  meUserId: string | null | undefined,
): boolean {
  if (!collabEnabled || !(access.isCollaborative || access.isReadOnly)) return true;
  if (askerOf(msg) === undefined) return access.canSend && access.isOwner === true;
  return canRegenerate(access, msg, meUserId);
}

/**
 * Whether the pending `ask_user_question` card is read-only for the viewer (F-1): only the person the
 * question was put to may answer. Unknown asker (`undefined`) stays interactive; the server still decides.
 */
export function askCardReadOnlyFor(
  requestedBy: MessageAuthor | null | undefined,
  meUserId: string | null | undefined,
  canSend: boolean,
): { name: string | null } | undefined {
  if (requestedBy === null) return { name: null };
  if (requestedBy === undefined) return canSend ? undefined : { name: null };
  if (!canSend || requestedBy.userId !== meUserId) return { name: requestedBy.displayName };
  return undefined;
}
