import { performance } from 'perf_hooks';
import { JournalEventInput } from '../domain/journal';
import {
  participantChanges,
  SessionFork,
  sessionAppend,
  sessionForkedEvent,
  sessionOwnerChangedEvent,
  sessionParticipantChangedEvent,
} from '../domain/session-participants';
import { JournalPreparer, JournalStore } from '../ports/journal-store';
import { SessionOwnerRecorder } from '../ports/principal-erasure';
import {
  ParticipantsChanged,
  SessionParticipantRecorder,
} from '../ports/session-participants';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

interface Journaled {
  orgId: string;
  sessionId: string;
  actorId: string;
  requestId?: string;
}

/**
 * Participant and fork events on the session's root stream (SS1), through the
 * same audited, sealed journal as every run event, so each lands in the org's
 * audit chain in the caller's transaction. A session that never ran durably
 * gets its stream here: the first durable run then reads who could see the
 * conversation before it.
 */
export class SessionParticipantJournal<Tx>
  implements SessionParticipantRecorder<Tx>, SessionOwnerRecorder<Tx>
{
  constructor(
    private readonly store: JournalStore<Tx>,
    private readonly preparer: JournalPreparer,
    private readonly logger: HarnessLogger = silentHarnessLogger,
    /** SIGNIN: a conversation shared with anyone ends a sign-in held in it (ARCH §4.9); best effort. */
    private readonly onShared?: (
      orgId: string,
      sessionId: string,
    ) => Promise<void>,
  ) {}

  async participantsChanged(
    change: ParticipantsChanged,
    tx: Tx,
  ): Promise<number> {
    const events = participantChanges(change.before, change.after).map((each) =>
      sessionParticipantChangedEvent(change.actorId, each),
    );
    if (events.length === 0) return 0;
    await this.append('session.participants_journaled', change, events, tx);
    if (change.after.isShared && change.after.shares.length > 0) {
      await this.onShared?.(change.orgId, change.sessionId).catch(
        () => undefined,
      );
    }
    return events.length;
  }

  async forked(
    orgId: string,
    sessionId: string,
    fork: SessionFork,
    tx: Tx,
  ): Promise<void> {
    await this.append(
      'session.fork_journaled',
      { orgId, sessionId: fork.fromSessionId, actorId: fork.actorId },
      [sessionForkedEvent(fork, sessionId)],
      tx,
    );
    await this.append(
      'session.fork_journaled',
      { orgId, sessionId, actorId: fork.actorId },
      [sessionForkedEvent(fork)],
      tx,
    );
  }

  async ownerChanged(
    orgId: string,
    sessionId: string,
    change: { by: string; fromUserId: string; toUserId: string },
    tx: Tx,
  ): Promise<void> {
    await this.append(
      'session.owner_journaled',
      { orgId, sessionId, actorId: change.toUserId },
      [sessionOwnerChangedEvent(change)],
      tx,
    );
  }

  private async append(
    event: string,
    ids: Journaled,
    events: JournalEventInput[],
    tx: Tx,
  ): Promise<void> {
    const started = performance.now();
    const fields = {
      orgId: ids.orgId,
      sessionId: ids.sessionId,
      actorId: ids.actorId,
      requestId: ids.requestId,
      changes: events.length,
    };
    try {
      const appended = await this.store.append(
        await this.preparer.prepare(
          sessionAppend(ids.orgId, ids.sessionId, events),
          tx,
        ),
        tx,
      );
      this.logger.info(
        event,
        harnessLogMeta(event, {
          ...fields,
          firstSeq: appended.firstSeq,
          lastSeq: appended.lastSeq,
          ms: elapsedMs(started),
        }),
      );
    } catch (error) {
      this.logger.warn(
        'session.participants_journal_failed',
        harnessLogMeta('session.participants_journal_failed', {
          ...fields,
          kind: event,
          errorType: error instanceof Error ? error.name : typeof error,
          ms: elapsedMs(started),
        }),
      );
      throw error;
    }
  }
}
