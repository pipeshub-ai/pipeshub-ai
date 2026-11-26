/**
 * Who could read a session, and since when (SS1, ARCHITECTURE 4.9 "Audit",
 * F11 3.3 and 3.10): every change to a session's participants, and a fork
 * into a new session, is an event on the session's root stream, written by
 * Node in the transaction that makes the change. They belong to no run, no
 * fold reads them (`critical: false`, so a worker that does not know them
 * ignores them), and every participant may see them (`visibility: shared`).
 */
import type { JournalAppend, JournalEventInput } from './journal';
import { principalRefFor } from './run-spec';
import {
  SessionParticipants,
  SessionRole,
  sessionRoleOf,
} from './session-roles';

/** A non-owner's role as the event records it; `none`: not a participant. */
export type ParticipantRole = Exclude<SessionRole, 'owner'> | 'none';

export interface ParticipantChange {
  /** The user whose role changed (an opaque id, never a name). */
  subjectId: string;
  role: ParticipantRole;
  prevRole: ParticipantRole;
}

const asParticipant = (role: SessionRole | null): ParticipantRole | null =>
  role === 'owner' ? null : (role ?? 'none');

/**
 * Every user whose role differs between two states of one session's
 * participants, by the same rule that grants the role (`sessionRoleOf`): a
 * share counts only while the session is shared, and an unknown level reads
 * as viewer. The owner never changes here. Sorted by user, so the events are
 * in a stable order.
 */
export function participantChanges(
  before: SessionParticipants,
  after: SessionParticipants,
): ParticipantChange[] {
  const users = new Set(
    [...before.shares, ...after.shares].map((share) => share.userId),
  );
  const changes: ParticipantChange[] = [];
  for (const subjectId of [...users].sort()) {
    const prevRole = asParticipant(sessionRoleOf(before, subjectId));
    const role = asParticipant(sessionRoleOf(after, subjectId));
    if (prevRole === null || role === null || prevRole === role) continue;
    changes.push({ subjectId, role, prevRole });
  }
  return changes;
}

export function sessionParticipantChangedEvent(
  actorId: string,
  change: ParticipantChange,
): JournalEventInput {
  return {
    type: 'session.participant_changed',
    schemaVersion: 1,
    critical: false,
    visibility: 'shared',
    data: {
      by: principalRefFor(actorId),
      subject: principalRefFor(change.subjectId),
      role: change.role,
      prevRole: change.prevRole,
    },
  };
}

export interface SessionFork {
  /** The forker, who owns the new session. */
  actorId: string;
  fromSessionId: string;
  /** The source root stream's last seq the copy accounts for. */
  atSeq: number;
}

/**
 * The fork as the new session records it (`shared`), or, given the new
 * session, as the source records it (`audit`: the other participants are not
 * told who continued privately, the audit chain is).
 */
export function sessionForkedEvent(
  fork: SessionFork,
  intoSessionId?: string,
): JournalEventInput {
  return {
    type: 'session.forked',
    schemaVersion: 1,
    critical: false,
    visibility: intoSessionId === undefined ? 'shared' : 'audit',
    data: {
      by: principalRefFor(fork.actorId),
      fromSessionId: fork.fromSessionId,
      atSeq: fork.atSeq,
      ...(intoSessionId !== undefined ? { intoSessionId } : {}),
    },
  };
}

/**
 * A shared session moved to an admin because its owner was deleted
 * (CLEANUP-G2): audit-only, the participants keep their roles.
 */
export function sessionOwnerChangedEvent(change: {
  by: string;
  fromUserId: string;
  toUserId: string;
}): JournalEventInput {
  return {
    type: 'session.owner_changed',
    schemaVersion: 1,
    critical: false,
    visibility: 'audit',
    data: {
      by: change.by,
      from: principalRefFor(change.fromUserId),
      to: principalRefFor(change.toUserId),
      reason: 'owner_deleted',
    },
  };
}

/** A session's own events go to its root stream and belong to no run. */
export const sessionAppend = (
  orgId: string,
  sessionId: string,
  events: JournalEventInput[],
): JournalAppend => ({
  orgId,
  sessionId,
  streamId: sessionId,
  writer: 'node',
  events,
});
