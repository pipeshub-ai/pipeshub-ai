import { ClientSession, FilterQuery } from 'mongoose';
import {
  IChatSession,
  IChatSessionActiveRun,
} from '../../../types/conversation.interfaces';
import { ConversationStatus } from '../../../constants/constants';
import { Caller } from '../domain/types';

export type SessionFilter = FilterQuery<IChatSession>;

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

export interface NewSessionLease {
  readonly activeRun: IChatSessionActiveRun;
  bind(sessionId: string): LeaseHandle;
}

export interface IRunLeaseManager {
  /** Throws ConversationBusyError (with the holder) when held and not expired, and a 404 when access is gone. */
  acquire(
    sessionId: string,
    caller: Caller,
    writeFilter: SessionFilter,
  ): Promise<LeaseHandle>;
  /** For brand-new sessions: the `activeRun` to embed in the create doc, plus its handle. */
  forNewSession(userId: string, orgId: string): NewSessionLease;
}

/** The run lost its lease (takeover or expiry) before a terminal write; the caller drops the write. */
export class LeaseLostError extends Error {
  constructor(readonly runId: string) {
    super(`Run ${runId} no longer holds the conversation lease`);
    this.name = 'LeaseLostError';
  }
}
