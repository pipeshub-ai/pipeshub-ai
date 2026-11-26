/** A session erasure's document as every store keeps it: no driver. */
import type {
  ErasureCounts,
  ErasureReason,
  ErasureStatus,
  ErasureStep,
} from '../../../../modules/agent-harness/domain/session-erasure';

export interface SessionErasureDocument {
  /** `orgId:sessionId`: one erasure per session, however often it is asked for. */
  _id: string;
  orgId: string;
  sessionId: string;
  reason: ErasureReason;
  by: string | null;
  status: ErasureStatus;
  step: ErasureStep | null;
  counts: ErasureCounts;
  attempt: number;
  failures: number;
  requestedAt: number;
  nextAttemptAt: number;
  completedAt: number | null;
  lastErrorType: string | null;
  holder: string | null;
  leaseExpiresAt: number | null;
}
