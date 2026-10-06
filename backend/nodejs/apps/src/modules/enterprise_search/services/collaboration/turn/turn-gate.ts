import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { DuplicateMessageError, RunLostError } from '../domain/errors';
import {
  claimLease,
  conversationContextOf,
} from '../http/conversation-context';
import { LeaseHandle, LeaseLostError } from '../leases/lease.types';
import { TurnLifecycle, TurnOutcome } from './turn-lifecycle';

/**
 * A handler's hold on its turn. With the flag off there is no lease, so `settle` does nothing and
 * the same exit paths run unchanged.
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
  if (conversationContextOf(req).collab !== true) {
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

/** The gate when the flag is off: nothing is held, so `settle` does nothing. */
export const unleasedGate = (): TurnGate => ({
  settle: () => Promise.resolve(),
});

/** How a turn that threw ends: a rejected duplicate returns the session to idle, a lost lease is a stop. */
export const outcomeForError = (error: unknown): TurnOutcome =>
  error instanceof DuplicateMessageError
    ? 'duplicate'
    : error instanceof LeaseLostError || error instanceof RunLostError
      ? 'stopped'
      : 'failed';
