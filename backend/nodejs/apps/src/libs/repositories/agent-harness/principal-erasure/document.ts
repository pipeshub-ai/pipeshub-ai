/** A principal erasure's document as every store keeps it: no driver. */
import type {
  OwnerTransfer,
  PrincipalErasureStep,
} from '../../../../modules/agent-harness/domain/principal-erasure';

/**
 * One deletion of an org (`<orgId>:org`) or of a user (`<orgId>:u:<userId>`)
 * on its way through its steps (CLEANUP-G1, G2). Ids, times and counts only;
 * removed once its last step is done. Deletions are rare, so the
 * collection needs no index.
 */
export interface PrincipalErasureDocument {
  _id: string;
  kind: 'org' | 'user';
  orgId: string;
  userId: string | null;
  by: string;
  step: PrincipalErasureStep;
  requestedAt: number;
  auditRetentionDays: number;
  auditRemoveAfter: number | null;
  cursor: string | null;
  transfers: OwnerTransfer[];
  journaled: number;
  counts: Record<string, number>;
  /** State the org's erasure steps keep on the job; absent or null: none. */
  extension?: Readonly<Record<string, unknown>> | null;
  failures: number;
  nextAttemptAt: number;
  lastErrorType: string | null;
}
