/**
 * Who a person is in a session, and what that lets them do (SS0,
 * ARCHITECTURE 4.9, F11 3.1). Before SS0 a share's `accessLevel: 'write'`
 * granted nothing beyond `read` and nothing said so; here each level has a
 * role, and each role's permissions are written down once.
 *
 * Pure, with opaque ids: the conversation store maps its documents onto
 * `SessionParticipants`.
 */
import type { ErrorCodeOf } from '../contracts/generated/ext/pipeshub/errors';
import type { ExecutorKind } from './run-spec';

export type SessionRole = 'owner' | 'collaborator' | 'viewer';

/** The levels a share stores today. */
export type ShareAccessLevel = 'read' | 'write';

export const ROLE_OF_ACCESS_LEVEL: Readonly<
  Record<ShareAccessLevel, Exclude<SessionRole, 'owner'>>
> = {
  read: 'viewer',
  write: 'collaborator',
};

export interface SessionParticipants {
  ownerId: string;
  isShared: boolean;
  shares: ReadonlyArray<{ userId: string; accessLevel: string }>;
}

/**
 * `actorId`'s role, or null when they are not a participant. A share is
 * honoured only while the session is shared; a level this code does not know
 * reads as `viewer`, the least a share can mean.
 */
export function sessionRoleOf(
  session: SessionParticipants,
  actorId: string,
): SessionRole | null {
  if (session.ownerId === actorId) return 'owner';
  if (!session.isShared) return null;
  const share = session.shares.find((entry) => entry.userId === actorId);
  if (share === undefined) return null;
  return share.accessLevel === 'write'
    ? ROLE_OF_ACCESS_LEVEL.write
    : ROLE_OF_ACCESS_LEVEL.read;
}

/** Why a participant may not send a turn: the catalogue's `turn` group (ERR-UX). */
export type TurnRefusal = ErrorCodeOf<'turn'>;

/**
 * Executors only a session's owner may start in a shared session (v1,
 * ARCHITECTURE 4.9 "Workspace"): an engine works in the session's one
 * workspace, whose files other participants' turns could not be filtered
 * from until per-actor workspaces (v2).
 */
export const OWNER_ONLY_EXECUTORS: ReadonlySet<ExecutorKind> = new Set([
  'engine',
]);

/** The roles that may send a turn, as a run records its actor's (SS2). */
export type TurnRole = Exclude<SessionRole, 'viewer'>;

/**
 * Whether `role` may send a turn (start a run, or steer their own) that runs
 * on `executor`, or why not. A collaborator's turn runs as the collaborator
 * (SS2, F11 3.2), and only where the org turned collaborator turns on: before
 * SS2 a `write` share granted nothing, so turning them on for every existing
 * share at once would be a silent widening (SS0's migration note). Engine
 * work stays the owner's (v1, one workspace per session).
 */
export function turnRefusal(
  role: SessionRole,
  executor: ExecutorKind = 'loop',
  collaboratorTurns = false,
): TurnRefusal | null {
  switch (role) {
    case 'owner':
      return null;
    case 'collaborator':
      if (OWNER_ONLY_EXECUTORS.has(executor)) return 'SHARED_SESSION_ENGINE';
      return collaboratorTurns ? null : 'COLLABORATOR_TURNS_DISABLED';
    case 'viewer':
      return 'VIEWER_READ_ONLY';
  }
}

/** Sharing, deleting and archiving a session: its owner only, whatever the share level. */
export const mayManageSession = (role: SessionRole | null): boolean =>
  role === 'owner';

export const TURN_REFUSAL_MESSAGES: Readonly<Record<TurnRefusal, string>> = {
  COLLABORATOR_TURNS_DISABLED:
    'Collaborators cannot send messages in this shared conversation. Ask the owner, or continue in a conversation of your own.',
  VIEWER_READ_ONLY:
    'This conversation was shared with you to read. Only its owner can send messages in it.',
  SHARED_SESSION_ENGINE:
    'Only the owner of a shared conversation can start work in its workspace. Continue in a conversation of your own.',
};
