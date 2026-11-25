import { canonicalDigest } from '../contracts/canonical-json';
import type { AuditEntry } from './audit';
import { principalRefFor } from './run-spec';

export const SESSION_REPLAYED_KIND = 'identity.session_replayed';

/**
 * A refresh token came back after it had been replaced, so the sign-in it belongs to was ended (ADR-WEB-02 D9).
 * Ids only: the family id names the sign-in, never a token; no address, which would outlive an erased person here.
 */
export interface SessionReplay {
  orgId: string;
  userId: string;
  familyId: string;
}

/** One sign-in's stream of the org's chain. */
export const sessionFamilyStreamId = (familyId: string): string =>
  `signin:${familyId}`;

/** The chain entry of an ended sign-in. `at` is the time in milliseconds, which orders the stream. */
export function sessionReplayAuditEntry(
  replay: SessionReplay,
  at: number,
): AuditEntry {
  return {
    kind: SESSION_REPLAYED_KIND,
    sessionId: null,
    streamId: sessionFamilyStreamId(replay.familyId),
    runId: null,
    journalSeq: at,
    writer: 'node',
    attemptId: null,
    fence: null,
    fields: {
      account: principalRefFor(replay.userId),
      // Names the sign-in so an admin can match it to the app log's auth.session_replay_detected (R46FIX-3).
      familyId: replay.familyId,
      actor: 'system',
      reason: 'refresh_reuse',
    },
    dataDigest: canonicalDigest(
      JSON.parse(JSON.stringify({ ...replay, at })) as Record<string, unknown>,
    ),
  };
}
