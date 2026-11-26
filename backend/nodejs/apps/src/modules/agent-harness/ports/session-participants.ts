import { SessionFork } from '../domain/session-participants';
import { SessionParticipants } from '../domain/session-roles';

/** One change to a session's participants, as the route that made it saw it. */
export interface ParticipantsChanged {
  orgId: string;
  sessionId: string;
  /** Who made the change: the route's verified caller, never a body field. */
  actorId: string;
  before: SessionParticipants;
  after: SessionParticipants;
  /** Correlation for the log line; never journaled. */
  requestId?: string;
}

/**
 * Records changes to who may read a session (SS1). The conversation module
 * calls it inside the transaction that changes the participants, so the
 * change and its record commit together; a failure must abort that
 * transaction.
 */
export interface SessionParticipantRecorder<Tx> {
  /** Journals every role that changed; resolves how many did. */
  participantsChanged(change: ParticipantsChanged, tx: Tx): Promise<number>;
  /**
   * Journals that `sessionId` was forked from `fork.fromSessionId` (SS3): on
   * the source's stream (for its audit) and as the new session's first event.
   */
  forked(
    orgId: string,
    sessionId: string,
    fork: SessionFork,
    tx: Tx,
  ): Promise<void>;
}
