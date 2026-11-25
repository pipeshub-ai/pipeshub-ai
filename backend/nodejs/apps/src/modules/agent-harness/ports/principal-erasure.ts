import {
  OwnerTransfer,
  PrincipalErasureJob,
  PrincipalErasureStep,
} from '../domain/principal-erasure';
import { HarnessRun } from '../domain/execution';

/** Org and user deletion jobs (CLEANUP-G1, G2), one per principal. */
export interface PrincipalErasureJobStore<Tx> {
  now(): Promise<number>;
  /** The org's deletion, at its first step; an org already being deleted keeps its job (false). */
  requestOrg(
    request: {
      orgId: string;
      by: string;
      requestedAt: number;
      auditRetentionDays: number;
      /** State the org's erasure steps keep on the job (`PrincipalErasureJob.extension`). */
      extension?: Readonly<Record<string, unknown>> | null;
    },
    tx: Tx,
  ): Promise<boolean>;
  /** A user's deletion, carrying the conversations moved to an admin. */
  requestUser(
    request: {
      orgId: string;
      userId: string;
      by: string;
      requestedAt: number;
      transfers: OwnerTransfer[];
    },
    tx: Tx,
  ): Promise<void>;
  findOrg(orgId: string): Promise<PrincipalErasureJob | null>;
  due(now: number, limit: number): Promise<PrincipalErasureJob[]>;
  /** Writes the job's progress; false when the job changed under it (removed, or another holder). */
  writeProgress(
    job: PrincipalErasureJob,
    expectedStep: PrincipalErasureStep,
    tx?: Tx,
  ): Promise<boolean>;
  remove(job: PrincipalErasureJob): Promise<void>;
}

/** What an org holds, as its deletion reads and erases it. */
export interface OrgDataStore<Tx> {
  /** Session ids of the org, in id order after `after`. */
  sessionsAfter(
    orgId: string,
    after: string | null,
    limit: number,
  ): Promise<string[]>;
  /** Marks the org's conversations of these ids deleted, so their erasure is never withdrawn (E7B). */
  markSessionsDeleted(
    orgId: string,
    sessionIds: readonly string[],
    tx: Tx,
  ): Promise<void>;
  pendingSessionErasures(orgId: string): Promise<number>;
  activeRuns(orgId: string): Promise<HarnessRun[]>;
  /** Every document of the org in the stores ORG_STORE_COVERAGE gives `org_stores`; counts by store. */
  eraseStores(orgId: string): Promise<Record<string, number>>;
  destroyDataKeys(orgId: string): Promise<number>;
  eraseAudit(orgId: string): Promise<number>;
}

/** The query service's org-wide erase: one bounded page per call. */
export interface OrgQueryServiceEraser {
  eraseOrg(orgId: string): Promise<{
    attachments: number;
    artifacts: number;
    blobs: number;
    skillCandidates: number;
    remaining: boolean;
  }>;
}

/**
 * What a deleted user owns in the control plane (CLEANUP-G2c): their
 * installations, their connections and their grant, through its own audited
 * writes. Idempotent; never another user's, a team's or the org's.
 */
export interface UserControlPlaneEraser {
  eraseUser(
    orgId: string,
    userId: string,
  ): Promise<{ installations: number; bindings: number; grants: number }>;
}

/** The sandbox broker's org-wide erase: every sandbox and folder of the org. */
export interface SandboxOrgEraser {
  eraseOrg(orgId: string): Promise<{ sandboxes: number; folders: number }>;
}

/** Tells the rest of the platform the org is gone (the entity-events outbox). */
export interface OrgDeletedAnnouncer {
  orgDeleted(orgId: string): Promise<void>;
}

/** Redis keys that name an org (spend counters, sign-in revocations). */
export interface OrgRedisEraser {
  eraseOrg(orgId: string): Promise<number>;
}

/** Records a conversation's move to another owner in its journal (and so the audit chain). */
export interface SessionOwnerRecorder<Tx> {
  ownerChanged(
    orgId: string,
    sessionId: string,
    change: { by: string; fromUserId: string; toUserId: string },
    tx: Tx,
  ): Promise<void>;
}
