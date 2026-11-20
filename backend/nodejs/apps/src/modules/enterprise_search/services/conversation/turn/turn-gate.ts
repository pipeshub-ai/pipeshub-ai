import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { DuplicateMessageError, RunLostError } from '../domain/errors';
import {
  claimLease,
  conversationContextOf,
} from '../http/conversation-context';
import { LeaseHandle, LeaseLostError } from './run-lease';
import { releaseRun, RunReservation } from '../../../utils/utils';
import { TurnLifecycle, TurnOutcome } from './turn-lifecycle';

/**
 * A handler's hold on its turn. Without a lease `settle` does nothing and the same exit paths run
 * unchanged.
 */
export interface TurnGate {
  readonly lease?: LeaseHandle;
  /** Stops the heartbeat and releases the lease; only the first call acts. */
  settle(outcome: TurnOutcome): Promise<void>;
}

/**
 * Takes the lease `runLease()` acquired and starts its heartbeat; `onLost` runs once when a renew
 * fails. Throws `LeaseLostError` when the response closed before the handler started.
 */
export function openTurnGate(
  req: AuthenticatedUserRequest,
  onLost: () => void,
): TurnGate {
  if (conversationContextOf(req).extended !== true) {
    return unleasedGate();
  }
  return holdLease(claimLease(req), onLost);
}

/** The gate for a first send, whose lease was created with the session and so never went through `runLease()`. */
export const holdLease = (lease: LeaseHandle, onLost: () => void): TurnGate => {
  const lifecycle = new TurnLifecycle(lease);
  lease.startHeartbeat(onLost);
  return { lease, settle: (outcome) => lifecycle.settle(outcome) };
};

/** The gate of a turn without a lease: nothing is held, so `settle` does nothing. */
export const unleasedGate = (): TurnGate => ({
  settle: () => Promise.resolve(),
});

/** The gate of a turn without a lease that reserved its thread with `reserveRun`: settling gives the thread back if the run left it in progress. */
export const reservedGate = (reservation: RunReservation | null): TurnGate => ({
  settle: () => releaseRun(reservation),
});

/** How a turn that threw ends: a rejected duplicate returns the session to idle, a lost lease is a stop. */
export const outcomeForError = (error: unknown): TurnOutcome =>
  error instanceof DuplicateMessageError
    ? 'duplicate'
    : error instanceof LeaseLostError || error instanceof RunLostError
      ? 'stopped'
      : 'failed';
