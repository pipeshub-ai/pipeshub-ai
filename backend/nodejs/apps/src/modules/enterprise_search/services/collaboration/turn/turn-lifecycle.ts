import { Logger } from '../../../../../libs/services/logger.service';
import {
  CONVERSATION_STATUS,
  ConversationStatus,
} from '../../../constants/constants';
import { LeaseHandle } from '../leases/lease.types';
import { conversationEventProducers } from '../notify/conversation-event-producers';

/** How a turn ended. `duplicate` (a rejected insert) and `unstarted` (the request ended before a handler took the lease) mean no turn ran, so the session goes back to the status it had (`Complete` when that was not a settled one). */
export type TurnOutcome =
  | 'completed'
  | 'failed'
  | 'stopped'
  | 'cancelled'
  | 'duplicate'
  | 'unstarted';

export function statusFor(outcome: TurnOutcome): ConversationStatus {
  switch (outcome) {
    case 'failed':
      return CONVERSATION_STATUS.FAILED;
    case 'stopped':
    case 'cancelled':
      return CONVERSATION_STATUS.STOPPED;
    case 'completed':
    case 'duplicate':
    case 'unstarted':
      return CONVERSATION_STATUS.COMPLETE;
  }
}

const defaultLogger = Logger.getInstance({ service: 'TurnLifecycle' });

/** The single exit for a turn: every terminal path calls `settle`, and only the first call acts. */
export class TurnLifecycle {
  private settled: Promise<void> | undefined;

  constructor(
    private readonly lease: LeaseHandle,
    private readonly logger: Pick<Logger, 'error'> = defaultLogger,
  ) {}

  settle(outcome: TurnOutcome): Promise<void> {
    this.settled ??= this.run(outcome);
    return this.settled;
  }

  private async run(outcome: TurnOutcome): Promise<void> {
    try {
      this.lease.stopHeartbeat();
      await this.lease.release(statusFor(outcome), {
        restorePrevious: outcome === 'duplicate' || outcome === 'unstarted',
      });
    } catch (error) {
      this.logger.error('Failed to release the conversation lease', {
        sessionId: this.lease.sessionId,
        runId: this.lease.runId,
        outcome,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    if (outcome === 'completed') {
      // Not awaited: telling collaborators must never hold up or fail the turn's exit.
      void Promise.resolve()
        .then(() =>
          conversationEventProducers().turnEnded({
            orgId: this.lease.orgId,
            sessionId: this.lease.sessionId,
            senderUserId: this.lease.userId,
          }),
        )
        .catch((error: unknown) => {
          this.logger.error('Failed to publish chat activity', {
            sessionId: this.lease.sessionId,
            error: error instanceof Error ? error.message : String(error),
          });
        });
    }
  }
}

/** The outcome a finished turn settles with, read from the status its terminal write left. */
export function outcomeForStatus(status: string | undefined): TurnOutcome {
  if (status === CONVERSATION_STATUS.FAILED) return 'failed';
  if (status === CONVERSATION_STATUS.STOPPED) return 'stopped';
  return 'completed';
}
