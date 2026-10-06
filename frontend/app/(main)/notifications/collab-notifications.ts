import type { TFunction } from 'i18next';
import type { NotificationListItem } from './api';

export const COLLAB_NOTIFICATION_TYPES = [
  'chat.shared',
  'chat.accessChanged',
  'chat.ownershipTransferred',
  'chat.deleted',
  'chat.activity',
  'chat.mentioned',
] as const;

export type CollabNotificationType = (typeof COLLAB_NOTIFICATION_TYPES)[number];

export const HANDOVER_NOTE_MAX_LENGTH = 140;

export function isCollabNotificationType(type: string): type is CollabNotificationType {
  return (COLLAB_NOTIFICATION_TYPES as readonly string[]).includes(type);
}

export function truncateNote(note: string, max = HANDOVER_NOTE_MAX_LENGTH): string {
  const chars = Array.from(note.trim());
  return chars.length <= max ? chars.join('') : `${chars.slice(0, max - 1).join('')}…`;
}

export interface CollabNotificationText {
  title: string;
  message: string;
}

function payloadOf(n: NotificationListItem): Record<string, unknown> {
  return n.payload && typeof n.payload === 'object' ? n.payload : {};
}

/** The chat a collaboration notification is about; `undefined` for other types or a malformed payload. */
export function collabSessionId(n: NotificationListItem): string | undefined {
  if (!isCollabNotificationType(n.type)) return undefined;
  const id = payloadOf(n).sessionId;
  return typeof id === 'string' && id ? id : undefined;
}

function contextOf(n: NotificationListItem): { chat?: string; actor?: string } {
  const ctx = n.context && typeof n.context === 'object' ? n.context : undefined;
  const clean = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : undefined);
  return { chat: clean(ctx?.chatTitle), actor: clean(ctx?.actorName) };
}

/** Picks the most specific i18n key for the context fields present, e.g. `bodyNamed` / `bodyNamedChat` / `bodyNamedActor`. */
function namedKey(base: string, { chat, actor }: { chat?: string; actor?: string }, allowActor = true): string {
  if (chat && actor && allowActor) return `${base}Named`;
  if (chat) return `${base}NamedChat`;
  if (actor && allowActor) return `${base}NamedActor`;
  return base;
}

/**
 * Localized text for the six collaboration types. The chat title and actor name appear only when
 * the list endpoint resolved them (`context`); otherwise the generic text is used. The note is
 * plain text (React escapes it on render). Returns `null` for any other type, so the caller keeps
 * the stored text.
 */
export function describeCollabNotification(
  n: NotificationListItem,
  t: TFunction,
): CollabNotificationText | null {
  if (!isCollabNotificationType(n.type)) return null;
  const payload = payloadOf(n);
  const level = payload.accessLevel === 'read' ? 'read' : 'write';
  const ctx = contextOf(n);
  const vars = { chat: ctx.chat, actor: ctx.actor };
  switch (n.type) {
    case 'chat.shared': {
      const rawNote = typeof payload.note === 'string' ? payload.note.trim() : '';
      const body = t(namedKey(`notifications.collab.shared.${level}`, ctx), vars);
      return {
        title: t('notifications.collab.shared.title'),
        message: rawNote
          ? `${body} ${t('notifications.collab.note', { note: truncateNote(rawNote) })}`
          : body,
      };
    }
    case 'chat.accessChanged':
      return {
        title: t('notifications.collab.accessChanged.title'),
        message: t(namedKey(`notifications.collab.accessChanged.${level}`, ctx, false), vars),
      };
    case 'chat.ownershipTransferred':
      return {
        title: t('notifications.collab.ownershipTransferred.title'),
        message: t(namedKey('notifications.collab.ownershipTransferred.body', ctx, false), vars),
      };
    case 'chat.deleted':
      return {
        title: t('notifications.collab.deleted.title'),
        message: t('notifications.collab.deleted.body'),
      };
    case 'chat.activity': {
      const total = typeof payload.count === 'number' && payload.count > 0 ? payload.count : 1;
      return {
        title: t('notifications.collab.activity.title'),
        message: t(namedKey('notifications.collab.activity.body', ctx, false), { total, ...vars }),
      };
    }
    case 'chat.mentioned':
      return {
        title: t('notifications.mention.title'),
        message: t(namedKey('notifications.mention.body', ctx), vars),
      };
  }
}
