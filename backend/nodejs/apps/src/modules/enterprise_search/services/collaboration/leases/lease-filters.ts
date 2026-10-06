import { FilterQuery, UpdateQuery } from 'mongoose';
import { IChatSession } from '../../../types/conversation.interfaces';

export const LEASE_TTL_MS = 120_000;
export const HEARTBEAT_INTERVAL_MS = 30_000;

/** Matches a session nobody is running a turn on: no lease, or one that expired at or before `now`. */
export function leaseFree(now: Date): FilterQuery<IChatSession> {
  return {
    $or: [
      { activeRun: null },
      { activeRun: { $exists: false } },
      { 'activeRun.leaseExpiresAt': { $lte: now } },
    ],
  };
}

export function leaseOwned(runId: string): FilterQuery<IChatSession> {
  return { 'activeRun.runId': runId };
}

/** Only the expiry moves: no `rev` bump, so a heartbeat is invisible to feed pollers. */
export function leaseRenewUpdate(
  now: Date,
  ttlMs: number = LEASE_TTL_MS,
): UpdateQuery<IChatSession> {
  return {
    $set: { 'activeRun.leaseExpiresAt': new Date(now.getTime() + ttlMs) },
  };
}
