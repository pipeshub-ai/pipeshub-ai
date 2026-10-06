import type { AccessView, ApiAccess, ConversationRole, MessageAuthor } from '../collaboration-types';

/** What the UI may offer. Everything here is the server's `access`, or today's rule when the flag is off. */
export interface ConversationAccess {
  /** `null` while the detail response has not arrived. */
  isOwner: boolean | null;
  role: ConversationRole | null;
  canSend: boolean;
  canManage: boolean;
  canInvite: boolean;
  /** Share is offered to anyone who can manage or invite. */
  canShare: boolean;
  isCollaborative: boolean;
  /** The server told the user they may only read (drives the banner; never true for legacy semantics). */
  isReadOnly: boolean;
}

export const OWNER_ACCESS_VIEW: AccessView = {
  role: 'owner',
  isOwner: true,
  accessLevel: 'owner',
  canSend: true,
  canManage: true,
  canInvite: true,
  isCollaborative: false,
};

function toRole(level: string | undefined, isOwner: boolean): ConversationRole {
  if (isOwner) return 'owner';
  return level === 'write' ? 'write' : 'read';
}

function isFullAccessView(a: ApiAccess): a is AccessView {
  return (
    typeof a.canSend === 'boolean' &&
    typeof a.canManage === 'boolean' &&
    typeof a.canInvite === 'boolean' &&
    typeof a.isCollaborative === 'boolean' &&
    typeof a.role === 'string'
  );
}

/**
 * Normalises `access` from a detail or list response. A flag-off server sends only
 * `{isOwner, accessLevel}`; the missing capabilities then follow today's owner-only rule.
 */
export function normalizeAccessView(api: ApiAccess | null | undefined): AccessView | null {
  if (!api) return null;
  if (isFullAccessView(api)) return api;
  const role = toRole(api.accessLevel, api.isOwner);
  return {
    role,
    isOwner: api.isOwner,
    accessLevel: role,
    canSend: api.isOwner,
    canManage: api.isOwner,
    canInvite: false,
    isCollaborative: false,
  };
}

/**
 * Flag off: today's semantics (`canSend = isOwner !== false`, manage = owner). Flag on: the server's view,
 * falling back to the legacy rule only while no view has arrived.
 */
export function toConversationAccess(
  api: AccessView | null | undefined,
  legacyIsOwner: boolean | null,
  flag: boolean,
): ConversationAccess {
  if (!flag || !api) {
    return {
      isOwner: legacyIsOwner,
      role: null,
      canSend: legacyIsOwner !== false,
      canManage: legacyIsOwner === true,
      canInvite: false,
      canShare: legacyIsOwner === true,
      isCollaborative: false,
      isReadOnly: false,
    };
  }
  return {
    isOwner: api.isOwner,
    role: api.role,
    canSend: api.canSend,
    canManage: api.canManage,
    canInvite: api.canInvite,
    canShare: api.canManage || api.canInvite,
    isCollaborative: api.isCollaborative,
    isReadOnly: !api.canSend && api.role === 'read',
  };
}

/**
 * The slot's `access` after the owner changed who the chat is shared with: `isCollaborative` follows the
 * number of other people and teams, so the open tab starts (or stops) syncing without a reload.
 * Returns the same object when nothing changes.
 */
export function withCollaboratorCount(access: AccessView | null | undefined, count: number): AccessView | null | undefined {
  if (!access || access.isCollaborative === count > 0) return access;
  return { ...access, isCollaborative: count > 0 };
}

/** Only the person who asked may regenerate an answer, the owner included (O-1/D8). */
export function canRegenerate(
  access: Pick<ConversationAccess, 'canSend'>,
  message: { requestedBy?: MessageAuthor | null; author?: MessageAuthor | null },
  meUserId: string | null | undefined,
): boolean {
  if (!access.canSend || !meUserId) return false;
  const asker = message.requestedBy ?? message.author;
  return asker?.userId === meUserId;
}
