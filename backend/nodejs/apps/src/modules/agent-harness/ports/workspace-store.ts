import { SealedContent } from '../domain/data-protection';
import { CommitReason, MergeStatus } from '../domain/workspace';

export interface ForkOrigin {
  workspaceId: string;
  revision: number;
}

/**
 * A keyed merge as the fork's head row keeps it: revisions only (the row holds
 * no path), so a replay recomputes the same plan from immutable revisions.
 */
export interface MergeRecord {
  key: string;
  into: string;
  /** The fork's merge base, its head, and the target's head the merge read. */
  base: number;
  theirs: number;
  ours: number;
  /** The target revision the merge wrote; null when it wrote none. */
  revision: number | null;
  status: MergeStatus;
}

/** A workspace's row in `agentWorkspaceHeads`. */
export interface WorkspaceHeadRecord {
  orgId: string;
  workspaceId: string;
  revision: number;
  /** Set when the workspace was created as a fork. */
  forkOf: ForkOrigin | null;
  /** The fork's own revision its last merge took; changes after it merge next. */
  mergeBase: number | null;
  /** The fork's last keyed merge (P6.4). */
  lastMerge?: MergeRecord | null;
}

/** One revision; the file list is sealed with the org key (`workspace.manifest`). */
export interface StoredManifest {
  orgId: string;
  workspaceId: string;
  revision: number;
  parentRevision: number | null;
  contentHash: string;
  fileCount: number;
  files: SealedContent;
  reason: CommitReason | 'fork' | 'upload';
  runId: string;
  createdAt: number;
}

/** A revision without its file list: what a version list shows. */
export type ManifestSummary = Omit<StoredManifest, 'files'>;

export interface WorkspaceAppend {
  manifest: StoredManifest;
  /** CAS: the head the writer read; 0 creates the workspace. */
  expectedRevision: number;
  /** Creating a fork: recorded on its head row, with merge base = its first revision. */
  forkOf?: ForkOrigin;
  /** A merge: the fork's merge base (and its keyed record) move in the same transaction. */
  mergedFork?: {
    workspaceId: string;
    mergeBase: number;
    record?: MergeRecord;
  };
}

/**
 * Manifests and heads. `append` is one transaction: the head moves only from
 * `expectedRevision` (else `WORKSPACE_CONFLICT`), and the manifest and any
 * fork bookkeeping land with it or not at all.
 */
export interface WorkspaceRepository {
  head(orgId: string, workspaceId: string): Promise<WorkspaceHeadRecord | null>;
  manifest(
    orgId: string,
    workspaceId: string,
    revision: number,
  ): Promise<StoredManifest | null>;
  append(write: WorkspaceAppend): Promise<void>;
  /**
   * Revisions newest first, at most `limit`, only those below `before` when
   * given; the sealed file lists are never read.
   */
  revisions(
    orgId: string,
    workspaceId: string,
    page: { limit: number; before?: number },
  ): Promise<ManifestSummary[]>;
  /** A merge that needed no new revision still consumes the fork's changes. */
  setMergeBase(
    orgId: string,
    forkId: string,
    mergeBase: number,
    record?: MergeRecord,
  ): Promise<void>;
}

/** One workspace's write lease (P6.4); the holder is a run of its session. */
export interface WriterLeaseRecord {
  orgId: string;
  sessionId: string;
  workspaceId: string;
  runId: string;
  expiresAt: number;
}

export type WriterAcquired =
  | { acquired: true; lease: WriterLeaseRecord; renewed: boolean }
  | { acquired: false; holder: string };

/**
 * At most one writer per workspace: `acquire` takes a free or expired lease,
 * renews the holder's own, and answers another live holder's name. Atomic
 * per workspace (two acquirers: one wins).
 */
export interface WorkspaceWriterStore {
  /** `takeOverFrom`: a holder known to have ended, whose live lease counts as free. */
  acquire(
    lease: WriterLeaseRecord,
    now: number,
    takeOverFrom?: string,
  ): Promise<WriterAcquired>;
  /** Frees the lease when `runId` holds it; true if it did. */
  release(orgId: string, workspaceId: string, runId: string): Promise<boolean>;
}

/** A session's wrapped workspace data key (D33); `wrapped` null once shredded. */
export interface WorkspaceKeyRecord {
  orgId: string;
  sessionId: string;
  keyId: string;
  wrapped: SealedContent | null;
  createdAt: number;
  destroyedAt: number | null;
}

export interface WorkspaceKeyStore {
  get(orgId: string, sessionId: string): Promise<WorkspaceKeyRecord | null>;
  /** false when the session already has a key (a concurrent issuer won) or a tombstone. */
  insert(record: WorkspaceKeyRecord): Promise<boolean>;
  /** Crypto-shred: drops the wrapped key and leaves a tombstone; true if a key was dropped. */
  destroy(orgId: string, sessionId: string, at: number): Promise<boolean>;
}
