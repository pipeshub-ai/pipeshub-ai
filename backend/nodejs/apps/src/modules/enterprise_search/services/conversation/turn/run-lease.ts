import { ClientSession, FilterQuery } from 'mongoose';
import { IChatSession } from '../../../types/conversation.interfaces';
import { ConversationStatus } from '../../../constants/constants';

export type SessionFilter = FilterQuery<IChatSession>;

/** A turn's exclusive hold on its conversation, for policies that serialize turns. */
export interface LeaseHandle {
  readonly runId: string;
  readonly sessionId: string;
  readonly orgId: string;
  /** The user whose turn holds the lease. */
  readonly userId: string;
  /** Extends the lease; false when this run no longer holds it. */
  renew(dbSession?: ClientSession | null): Promise<boolean>;
  /** Idempotent; clears only its own lease (conditional on `activeRun.runId`). */
  release(status: ConversationStatus, options?: ReleaseOptions): Promise<void>;
  /** Filter fragment that makes a session write conditional on still holding the lease. */
  ownershipFilter(): SessionFilter;
  /** Renews every 30 s; a failed or unmatched renew stops the heartbeat and calls `onLost` once. */
  startHeartbeat(onLost: () => void): void;
  stopHeartbeat(): void;
}

export interface ReleaseOptions {
  /** No turn ran: put back the status and `failReason` the session had before `acquire`, when it had one. */
  restorePrevious?: boolean;
}

/** The run lost its lease (takeover or expiry) before a terminal write; the caller drops the write. */
export class LeaseLostError extends Error {
  constructor(readonly runId: string) {
    super(`Run ${runId} no longer holds the conversation lease`);
    this.name = 'LeaseLostError';
  }
}
