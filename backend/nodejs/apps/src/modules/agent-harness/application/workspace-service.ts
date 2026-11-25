import { createHash } from 'node:crypto';
import { AttemptClaims } from '../domain/authority';
import { HarnessConflictError, HarnessNotFoundError } from '../domain/errors';
import {
  attemptMayReadRun,
  attemptOwnsRun,
  ChildOf,
  HarnessRun,
  TERMINAL_RUN_STATUSES,
} from '../domain/execution';
import {
  applyChanges,
  changedPaths,
  changeKinds,
  childForkOf,
  CommitReason,
  ConflictKind,
  conflictKind,
  compareCodePoints,
  ConflictPolicy,
  FileChangeKind,
  isEmptyChange,
  manifestContentHash,
  manifestSealContext,
  MAX_WORKSPACE_FILES,
  MergePlan,
  MergeStatus,
  PathConflict,
  planMerge,
  principalWorkspaceId,
  sessionWorkspaceId,
  sortedFiles,
  validateChanges,
  WorkspaceChanges,
  WorkspaceError,
  WorkspaceFileEntry,
  WorkspaceFiles,
  workspaceInSession,
  WorkspaceManifestView,
  workspaceOfPrincipal,
  WorkspacePrincipal,
  workspaceWritableBy,
} from '../domain/workspace';
import {
  ManifestSummary,
  MergeRecord,
  StoredManifest,
  WorkspaceAppend,
  WorkspaceRepository,
  WorkspaceWriterStore,
} from '../ports/workspace-store';
import { EnvelopeCipher } from './envelope-cipher';
import { RunReader } from './harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
  silentHarnessLogger,
} from './harness-log';
import {
  IssuedWorkspaceKey,
  SessionWorkspaceKeys,
} from './session-workspace-keys';
import { ObjectReseal } from './workspace-object-resealer';
import { mapWithConcurrency } from '../../../libs/utils/concurrency.util';

export interface WorkspaceMergeResult {
  status: MergeStatus;
  forkId: string;
  into: string;
  manifest: WorkspaceManifestView;
  conflicts: PathConflict[];
  repositoryConflicts: string[];
  /** The paths the merge wrote or deleted in the target. */
  merged: string[];
  /** How the merge changed each `merged` path in the target (R27REST-FU-1). */
  kinds: Record<string, FileChangeKind>;
  /** The fork revision the merge took (holds the fork's side of each conflict). */
  forkRevision: number;
}

export interface WorkspaceMergeRequest {
  forkId: string;
  /** The same key again answers the recorded merge and writes nothing (P6.4). */
  mergeKey?: string;
  onConflict?: ConflictPolicy;
}

/** A writer lease as a worker sees it; `acquired: false` names the live holder. */
export type WriterLeaseView =
  | {
      workspaceId: string;
      acquired: true;
      holder: string;
      expiresAt: number;
      renewed: boolean;
    }
  | { workspaceId: string; acquired: false; holder: string };

const mergedPaths = (changes: WorkspaceChanges): string[] =>
  [...changes.upserts.map((entry) => entry.path), ...changes.deletes].sort(
    compareCodePoints,
  );

interface MergeOutcome {
  conflicts: PathConflict[];
  repositoryConflicts: string[];
  merged: string[];
  kinds: Record<string, FileChangeKind>;
}

/** What a merge plan writes into a target that held `ours`. */
const mergeOutcome = (plan: MergePlan, ours: WorkspaceFiles): MergeOutcome => ({
  conflicts: plan.conflicts,
  repositoryConflicts: plan.repositoryConflicts,
  merged: mergedPaths(plan.changes),
  kinds: changeKinds(plan.changes, ours),
});

const NOTHING_MERGED: MergeOutcome = {
  conflicts: [],
  repositoryConflicts: [],
  merged: [],
  kinds: {},
};

/** A child's merge as the parent's timeline shows it (P6.4), from the fork's recorded merge. */
export interface ChildMergeReport {
  status: MergeStatus;
  into: string;
  /** The target revision the merge wrote; null when it wrote none. */
  revision: number | null;
  /** The fork revision holding the child's side of each conflict. */
  forkRevision: number;
  merged: string[];
  /** Paths and kinds only: which side holds what is the fork revision's to show. */
  conflicts: Array<{ path: string; kind: ConflictKind }>;
  repositoryConflicts: string[];
}

/** One revision as a version list shows it: never its files. */
export interface WorkspaceVersionView {
  revision: number;
  parentRevision: number | null;
  reason: string;
  createdAt: number;
  fileCount: number;
  contentHash: string;
  /** Written by a person restoring an earlier revision, not by a run. */
  restoredByUser: boolean;
  /** The run that wrote it (a turn, a `/run`); absent when a person's restore, upload or a fork did. */
  runId?: string;
  /** An `upload` revision's added or replaced paths, sorted: what its chat row lists after a reload. */
  paths?: string[];
}

export interface WorkspaceVersions {
  workspaceId: string;
  head: number;
  versions: WorkspaceVersionView[];
}

export interface SessionWorkspaceScope {
  orgId: string;
  sessionId: string;
}

export interface RestoreWorkspaceVersion extends SessionWorkspaceScope {
  actorId: string;
  revision: number;
  /** The head the person saw: the restore lands on it or not at all (CAS). */
  baseRevision: number;
  idempotencyKey: string;
}

export interface RestoredWorkspaceVersion {
  workspaceId: string;
  revision: number;
  restoredFrom: number;
  replayed: boolean;
}

/**
 * A person's restore holds the writer lease under this prefix; it is never
 * taken over as if it were a run that ended (its lease is short instead).
 */
export const RESTORE_HOLDER_PREFIX = 'restore:';

/** A person's `/upload` (SLASH3): its own holder, so `/undo` counts it as a turn and takes back only it. */
export const UPLOAD_HOLDER_PREFIX = 'upload:';

export interface UploadWorkspaceFiles extends SessionWorkspaceScope {
  actorId: string;
  /** The head the person saw: the upload lands on it or not at all (CAS). */
  baseRevision: number;
  idempotencyKey: string;
  /** Already sealed with the session's key and stored. */
  files: WorkspaceFileEntry[];
}

export interface UploadedWorkspaceFiles {
  workspaceId: string;
  revision: number;
  replayed: boolean;
}

/** Who wrote a forked session's first revision (SS3): the fork, not a run. */
export const SESSION_FORK_HOLDER_PREFIX = 'session-fork:';
/** Every byte is copied under the new key in the request; more is refused, never truncated. */
export const SESSION_FORK_MAX_WORKSPACE_BYTES = 512 * 1024 * 1024;
const SESSION_FORK_OBJECT_CONCURRENCY = 8;

export interface SessionWorkspaceFork {
  orgId: string;
  fromSessionId: string;
  toSessionId: string;
  actorId: string;
}

export interface ForkedSessionWorkspace {
  workspaceId: string;
  revision: number;
  files: number;
  replayed: boolean;
}
const RESTORE_LEASE_MS = 30_000;
const MAX_REVISIONS_PAGE = 100;
const LAST_TURN_PAGES = 10;

type Access = 'read' | 'write';

/** Who writes: a worker's run (attempt-bound) or a person's restore (no attempt). */
interface Bound {
  run: Pick<
    HarnessRun,
    'orgId' | 'sessionId' | 'runId' | 'attemptId' | 'fence'
  > & { parent?: ChildOf | null };
  attempt: AttemptClaims | null;
}

/** An attempt's binding: its run as stored, whose principal decides the workspaces it reaches (OWD-16). */
type AttemptBound = Bound & { run: HarnessRun };

const sessionBound = (scope: SessionWorkspaceScope, holder: string): Bound => ({
  run: {
    orgId: scope.orgId,
    sessionId: scope.sessionId,
    runId: holder,
    attemptId: null,
    fence: 0,
  },
  attempt: null,
});

const notFound = (workspaceId: string, revision: number): WorkspaceError =>
  new WorkspaceError(
    'WORKSPACE_REVISION_NOT_FOUND',
    `${workspaceId} has no revision ${String(revision)}`,
  );

const emptyView = (workspaceId: string): WorkspaceManifestView => ({
  workspaceId,
  revision: 0,
  parentRevision: null,
  contentHash: manifestContentHash(new Map()),
  files: [],
  createdAt: 0,
});

const versionView = (row: ManifestSummary): WorkspaceVersionView => ({
  revision: row.revision,
  parentRevision: row.parentRevision,
  reason: row.reason,
  createdAt: row.createdAt,
  fileCount: row.fileCount,
  contentHash: row.contentHash,
  restoredByUser: row.runId.startsWith(RESTORE_HOLDER_PREFIX),
  ...(PERSON_HOLDER_PREFIXES.some((prefix) => row.runId.startsWith(prefix))
    ? {}
    : { runId: row.runId }),
});

/** Holders that are a person's action or a fork, never a run. */
const PERSON_HOLDER_PREFIXES = [
  RESTORE_HOLDER_PREFIX,
  UPLOAD_HOLDER_PREFIX,
  SESSION_FORK_HOLDER_PREFIX,
];

const filesOf = (
  view: WorkspaceManifestView,
): Map<string, WorkspaceFileEntry> =>
  new Map(view.files.map((entry) => [entry.path, entry]));

export interface WorkspaceServiceOptions {
  runs: RunReader;
  repository: WorkspaceRepository;
  cipher: EnvelopeCipher;
  keys: SessionWorkspaceKeys;
  writers: WorkspaceWriterStore;
  logger?: HarnessLogger;
  now?: () => number;
}

/**
 * Session workspaces for harness workers (P3.2). The attempt token says who
 * asks; the run store says whether that attempt may still write (it owns the
 * run) or read; the run's session bounds which workspaces it may touch
 * (`ws:{sessionId}` and its forks). File lists are sealed at rest with the
 * org key; objects are sealed by the worker with the session key (D33).
 */
export class WorkspaceService {
  private readonly runs: RunReader;
  private readonly repository: WorkspaceRepository;
  private readonly cipher: EnvelopeCipher;
  private readonly keys: SessionWorkspaceKeys;
  private readonly writers: WorkspaceWriterStore;
  private readonly logger: HarnessLogger;
  private readonly now: () => number;

  constructor(options: WorkspaceServiceOptions) {
    this.runs = options.runs;
    this.repository = options.repository;
    this.cipher = options.cipher;
    this.keys = options.keys;
    this.writers = options.writers;
    this.logger = options.logger ?? silentHarnessLogger;
    this.now = options.now ?? Date.now;
  }

  async head(
    attempt: AttemptClaims,
    workspaceId: string,
  ): Promise<WorkspaceManifestView> {
    const bound = await this.bind(attempt, 'read', workspaceId);
    return this.headView(bound, workspaceId);
  }

  async revision(
    attempt: AttemptClaims,
    workspaceId: string,
    revision: number,
  ): Promise<WorkspaceManifestView> {
    const bound = await this.bind(attempt, 'read', workspaceId);
    return this.view(bound, workspaceId, revision);
  }

  async commit(
    attempt: AttemptClaims,
    workspaceId: string,
    request: {
      baseRevision: number;
      reason: CommitReason;
      changes: WorkspaceChanges;
    },
  ): Promise<WorkspaceManifestView> {
    const bound = await this.bind(attempt, 'write', workspaceId);
    this.assertWritable(bound, workspaceId);
    const changes = this.validated(bound, workspaceId, request.changes);
    const head = await this.headView(bound, workspaceId);
    if (head.revision !== request.baseRevision) {
      throw this.conflict(
        bound,
        workspaceId,
        request.baseRevision,
        head.revision,
      );
    }
    if (isEmptyChange(changes)) return head;
    const files = this.applied(bound, workspaceId, filesOf(head), changes);
    return this.append(bound, head, files, request.reason);
  }

  async fork(
    attempt: AttemptClaims,
    workspaceId: string,
    request: { atRevision: number; forkId: string },
  ): Promise<WorkspaceManifestView> {
    const bound = await this.bind(
      attempt,
      'write',
      workspaceId,
      request.forkId,
    );
    this.assertWritable(bound, request.forkId);
    return this.forkAt(bound, workspaceId, request);
  }

  private async forkAt(
    bound: Bound,
    workspaceId: string,
    request: { atRevision: number; forkId: string },
  ): Promise<WorkspaceManifestView> {
    const { forkId, atRevision } = request;
    if (forkId === workspaceId) {
      throw this.forkConflict(bound, forkId, 'self');
    }
    const existing = await this.existingFork(bound, workspaceId, request);
    if (existing !== null) return existing;
    const source = await this.view(bound, workspaceId, atRevision);
    let forked: WorkspaceManifestView;
    try {
      forked = await this.append(
        bound,
        emptyView(forkId),
        filesOf(source),
        'fork',
        { forkOf: { workspaceId, revision: atRevision } },
      );
    } catch (error) {
      // A concurrent retry of the same fork created it first: same answer.
      const raced =
        error instanceof WorkspaceError && error.code === 'WORKSPACE_CONFLICT'
          ? await this.existingFork(bound, workspaceId, request)
          : null;
      if (raced === null) throw error;
      return raced;
    }
    this.logger.info(
      'workspace.forked',
      harnessLogMeta('workspace.forked', {
        ...this.fields(bound),
        workspaceId,
        forkId,
        atRevision,
      }),
    );
    return forked;
  }

  /**
   * Three-way merge of a fork into its origin. `refuse` merges all or nothing;
   * `keepTarget` merges what does not conflict and keeps the target's side of
   * each conflict (`partial`). With a `mergeKey` the merge is recorded on the
   * fork (revisions only) and the same key answers it again, writing nothing.
   */
  async merge(
    attempt: AttemptClaims,
    into: string,
    request: WorkspaceMergeRequest,
  ): Promise<WorkspaceMergeResult> {
    const { forkId, mergeKey } = request;
    const policy = request.onConflict ?? 'refuse';
    const bound = await this.bind(attempt, 'write', into, forkId);
    this.assertWritable(bound, into);
    const fork = await this.repository.head(bound.run.orgId, forkId);
    if (fork?.forkOf?.workspaceId !== into || fork.mergeBase === null) {
      throw new WorkspaceError(
        'WORKSPACE_NOT_A_FORK',
        `${forkId} is not a fork of ${into}`,
      );
    }
    const last = fork.lastMerge ?? null;
    if (
      mergeKey !== undefined &&
      last?.key === mergeKey &&
      last.into === into
    ) {
      return this.replayedMerge(bound, forkId, last);
    }
    const base = filesOf(await this.view(bound, forkId, fork.mergeBase));
    const theirs = await this.view(bound, forkId, fork.revision);
    const ours = await this.headView(bound, into);
    const record = (
      status: MergeStatus,
      revision: number | null,
    ): MergeRecord | undefined =>
      mergeKey === undefined
        ? undefined
        : {
            key: mergeKey,
            into,
            base: fork.mergeBase ?? 0,
            theirs: theirs.revision,
            ours: ours.revision,
            revision,
            status,
          };
    const result = (
      status: MergeStatus,
      manifest: WorkspaceManifestView,
      outcome: MergeOutcome = NOTHING_MERGED,
    ): WorkspaceMergeResult => ({
      status,
      forkId,
      into,
      manifest,
      ...outcome,
      forkRevision: theirs.revision,
    });
    if (changedPaths(base, filesOf(theirs)).length === 0) {
      await this.repository.setMergeBase(
        bound.run.orgId,
        forkId,
        theirs.revision,
        record('up_to_date', null),
      );
      return result('up_to_date', ours);
    }
    const plan = planMerge(base, filesOf(ours), filesOf(theirs));
    const conflicted =
      plan.conflicts.length > 0 || plan.repositoryConflicts.length > 0;
    const fields = {
      ...this.fields(bound),
      workspaceId: into,
      forkId,
      revision: ours.revision,
      forkRevision: theirs.revision,
      onConflict: policy,
      ...(mergeKey !== undefined ? { mergeKey } : {}),
      conflicts: plan.conflicts.length,
      repositoryConflicts: plan.repositoryConflicts.length,
    };
    if (conflicted) {
      this.logger.info(
        'workspace.merge_conflict',
        harnessLogMeta('workspace.merge_conflict', fields),
      );
      if (policy === 'refuse') {
        return result('conflict', ours, {
          ...NOTHING_MERGED,
          conflicts: plan.conflicts,
          repositoryConflicts: plan.repositoryConflicts,
        });
      }
    }
    const status: MergeStatus = conflicted ? 'partial' : 'merged';
    const outcome = mergeOutcome(plan, filesOf(ours));
    if (isEmptyChange(plan.changes)) {
      await this.repository.setMergeBase(
        bound.run.orgId,
        forkId,
        theirs.revision,
        record(status, null),
      );
      return result(status, ours, outcome);
    }
    const merged = await this.append(
      bound,
      ours,
      this.applied(bound, into, filesOf(ours), plan.changes),
      'merge',
      {
        mergedFork: {
          workspaceId: forkId,
          mergeBase: theirs.revision,
          record: record(status, ours.revision + 1),
        },
      },
    );
    this.logger.info(
      'workspace.merged',
      harnessLogMeta('workspace.merged', {
        ...fields,
        status,
        revision: merged.revision,
        upserts: plan.changes.upserts.length,
        deletes: plan.changes.deletes.length,
      }),
    );
    return result(status, merged, outcome);
  }

  /** A recorded merge's plan, recomputed from the immutable revisions it read. */
  private async recordedPlan(
    bound: Bound,
    forkId: string,
    record: MergeRecord,
  ): Promise<MergeOutcome> {
    if (record.status === 'up_to_date') return NOTHING_MERGED;
    const ours = filesOf(await this.view(bound, record.into, record.ours));
    const plan = planMerge(
      filesOf(await this.view(bound, forkId, record.base)),
      ours,
      filesOf(await this.view(bound, forkId, record.theirs)),
    );
    return mergeOutcome(plan, ours);
  }

  /** A keyed merge asked again: recomputed from the revisions it recorded. */
  private async replayedMerge(
    bound: Bound,
    forkId: string,
    record: MergeRecord,
  ): Promise<WorkspaceMergeResult> {
    const { into } = record;
    const manifest = await this.view(
      bound,
      into,
      record.revision ?? record.ours,
    );
    const plan = await this.recordedPlan(bound, forkId, record);
    this.logger.info(
      'workspace.merge_replayed',
      harnessLogMeta('workspace.merge_replayed', {
        ...this.fields(bound),
        workspaceId: into,
        forkId,
        mergeKey: record.key,
        status: record.status,
        revision: manifest.revision,
      }),
    );
    return {
      status: record.status,
      forkId,
      into,
      manifest,
      ...plan,
      forkRevision: record.theirs,
    };
  }

  /**
   * The write lease of a workspace (P6.4), held by the attempt's run until
   * `ttlMs` from now; renewing is acquiring again. Another live holder is
   * answered by name, never queued.
   */
  async acquireWriter(
    attempt: AttemptClaims,
    workspaceId: string,
    request: { ttlMs: number },
  ): Promise<WriterLeaseView> {
    const bound = await this.bind(attempt, 'write', workspaceId);
    this.assertWritable(bound, workspaceId);
    const now = this.now();
    const { orgId, sessionId, runId } = bound.run;
    const lease = {
      orgId,
      sessionId,
      workspaceId,
      runId,
      expiresAt: now + request.ttlMs,
    };
    let answer = await this.writers.acquire(lease, now);
    if (!answer.acquired && !answer.holder.startsWith(RESTORE_HOLDER_PREFIX)) {
      // A holder that ended (a crashed writer) frees its lease now, not at expiry.
      const holder = await this.runs.get(orgId, answer.holder);
      if (holder === null || TERMINAL_RUN_STATUSES.has(holder.status)) {
        answer = await this.writers.acquire(lease, now, answer.holder);
        if (answer.acquired) {
          this.logger.info(
            'workspace.writer_taken_over',
            harnessLogMeta('workspace.writer_taken_over', {
              ...this.fields(bound),
              workspaceId,
              endedHolder: holder?.runId ?? null,
            }),
          );
        }
      }
    }
    if (!answer.acquired) {
      this.logger.warn(
        'workspace.writer_busy',
        harnessLogMeta('workspace.writer_busy', {
          ...this.fields(bound),
          workspaceId,
          holder: answer.holder,
        }),
      );
      return { workspaceId, acquired: false, holder: answer.holder };
    }
    if (!answer.renewed) {
      this.logger.info(
        'workspace.writer_acquired',
        harnessLogMeta('workspace.writer_acquired', {
          ...this.fields(bound),
          workspaceId,
          expiresAt: answer.lease.expiresAt,
        }),
      );
    }
    return {
      workspaceId,
      acquired: true,
      holder: runId,
      expiresAt: answer.lease.expiresAt,
      renewed: answer.renewed,
    };
  }

  async releaseWriter(
    attempt: AttemptClaims,
    workspaceId: string,
  ): Promise<{ workspaceId: string; released: boolean }> {
    const bound = await this.bind(attempt, 'write', workspaceId);
    const released = await this.writers.release(
      bound.run.orgId,
      workspaceId,
      bound.run.runId,
    );
    if (released) {
      this.logger.info(
        'workspace.writer_released',
        harnessLogMeta('workspace.writer_released', {
          ...this.fields(bound),
          workspaceId,
        }),
      );
    }
    return { workspaceId, released };
  }

  /**
   * Admission's fork for a child (P6.4): `forkId` of `origin` at its head, or
   * the fork a crashed admission already made (whatever revision it holds).
   * Node does it for the parent's attempt; the child never writes `origin`.
   */
  async forkForChild(
    attempt: AttemptClaims,
    origin: string,
    forkId: string,
  ): Promise<{ workspaceId: string; revision: number }> {
    const bound = await this.bind(attempt, 'write', origin, forkId);
    const existing = await this.repository.head(bound.run.orgId, forkId);
    if (existing !== null) {
      if (existing.forkOf?.workspaceId !== origin) {
        throw this.forkConflict(
          bound,
          forkId,
          existing.forkOf === null ? 'not_a_fork' : 'other_origin',
        );
      }
      return { workspaceId: origin, revision: existing.forkOf.revision };
    }
    const head = await this.headView(bound, origin);
    try {
      await this.forkAt(bound, origin, { atRevision: head.revision, forkId });
      return { workspaceId: origin, revision: head.revision };
    } catch (error) {
      // A concurrent admission of the same child forked first, at another head.
      const raced =
        error instanceof WorkspaceError &&
        error.code === 'WORKSPACE_FORK_CONFLICT'
          ? await this.repository.head(bound.run.orgId, forkId)
          : null;
      if (raced?.forkOf?.workspaceId !== origin) throw error;
      return { workspaceId: origin, revision: raced.forkOf.revision };
    }
  }

  async rollback(
    attempt: AttemptClaims,
    workspaceId: string,
    request: { toRevision: number },
  ): Promise<WorkspaceManifestView> {
    const bound = await this.bind(attempt, 'write', workspaceId);
    this.assertWritable(bound, workspaceId);
    const target = await this.view(bound, workspaceId, request.toRevision);
    const head = await this.headView(bound, workspaceId);
    const rolled = await this.append(bound, head, filesOf(target), 'rollback');
    this.logger.info(
      'workspace.rolled_back',
      harnessLogMeta('workspace.rolled_back', {
        ...this.fields(bound),
        workspaceId,
        revision: rolled.revision,
        toRevision: request.toRevision,
      }),
    );
    return rolled;
  }

  /** The session workspace's revisions, newest first (U3); metadata only. */
  async versions(
    scope: SessionWorkspaceScope,
    page: { limit: number; before?: number },
  ): Promise<WorkspaceVersions> {
    const workspaceId = sessionWorkspaceId(scope.sessionId);
    const [head, rows] = await Promise.all([
      this.repository.head(scope.orgId, workspaceId),
      this.repository.revisions(scope.orgId, workspaceId, page),
    ]);
    const bound = sessionBound(scope, 'viewer');
    return {
      workspaceId,
      head: head?.revision ?? 0,
      versions: await Promise.all(
        rows.map(async (row) =>
          row.reason === 'upload'
            ? {
                ...versionView(row),
                paths: await this.uploadedPaths(bound, workspaceId, row),
              }
            : versionView(row),
        ),
      ),
    };
  }

  /** The paths an `upload` revision wrote: its files that its parent lacks or held with other content. */
  private async uploadedPaths(
    bound: Bound,
    workspaceId: string,
    row: ManifestSummary,
  ): Promise<string[]> {
    const [after, before] = await Promise.all([
      this.view(bound, workspaceId, row.revision),
      this.view(bound, workspaceId, row.parentRevision ?? 0),
    ]);
    const parent = filesOf(before);
    return after.files
      .filter(
        (entry) => parent.get(entry.path)?.contentHash !== entry.contentHash,
      )
      .map((entry) => entry.path)
      .sort();
  }

  /** The session workspace's files at `revision` (0: none yet), or at its head; for a person's view (SLASH2 `/diff`). */
  async sessionFiles(
    scope: SessionWorkspaceScope,
    revision?: number,
  ): Promise<WorkspaceManifestView> {
    const workspaceId = sessionWorkspaceId(scope.sessionId);
    const bound = sessionBound(scope, 'viewer');
    return revision === undefined
      ? this.headView(bound, workspaceId)
      : this.view(bound, workspaceId, revision);
  }

  /**
   * The revisions the latest turn that wrote the session workspace made, as
   * `from` (the revision before it) and `to` (the head): the head and the
   * revisions under it that the same run wrote (SLASH2 `/undo`). Null when
   * nothing was written, or when a person's restore or a fork wrote the head.
   */
  async lastTurn(
    scope: SessionWorkspaceScope,
  ): Promise<{ from: number; to: number } | null> {
    const workspaceId = sessionWorkspaceId(scope.sessionId);
    let runId: string | null = null;
    let from = 0;
    let to = 0;
    let before: number | undefined;
    // shortcut: a turn with more than LAST_TURN_PAGES pages of revisions is undone to its oldest one read; page further if that shows up.
    for (let page = 0; page < LAST_TURN_PAGES; page += 1) {
      const rows = await this.repository.revisions(scope.orgId, workspaceId, {
        limit: MAX_REVISIONS_PAGE,
        ...(before !== undefined ? { before } : {}),
      });
      for (const row of rows) {
        if (runId === null) {
          if (
            row.runId.startsWith(RESTORE_HOLDER_PREFIX) ||
            row.runId.startsWith(SESSION_FORK_HOLDER_PREFIX)
          ) {
            return null;
          }
          runId = row.runId;
          to = row.revision;
        } else if (row.runId !== runId) {
          return { from, to };
        }
        from = row.parentRevision ?? 0;
      }
      if (rows.length < MAX_REVISIONS_PAGE) break;
      before = rows[rows.length - 1]?.revision;
    }
    return runId === null ? null : { from, to };
  }

  /**
   * A person restores an earlier revision of the session workspace (U3): its
   * files become a new revision on top of the head they saw. History is only
   * appended to; a moved head is a conflict and a live writer (an engine at
   * work) refuses it, as for any other writer (P6.4).
   */
  async restore(
    request: RestoreWorkspaceVersion,
  ): Promise<RestoredWorkspaceVersion> {
    const workspaceId = sessionWorkspaceId(request.sessionId);
    const holder = `${RESTORE_HOLDER_PREFIX}${createHash('sha256')
      .update(
        [
          request.orgId,
          request.sessionId,
          request.actorId,
          String(request.revision),
          String(request.baseRevision),
          request.idempotencyKey,
        ].join('\0'),
      )
      .digest('hex')
      .slice(0, 32)}`;
    const bound = sessionBound(request, holder);
    const refuse = (error: WorkspaceError): WorkspaceError => {
      this.logger.warn(
        'workspace.restore_refused',
        harnessLogMeta('workspace.restore_refused', {
          ...this.fields(bound),
          workspaceId,
          reason: error.code,
          toRevision: request.revision,
          baseRevision: request.baseRevision,
        }),
      );
      return error;
    };
    if (request.revision < 1) {
      throw refuse(notFound(workspaceId, request.revision));
    }
    let target: WorkspaceManifestView;
    try {
      target = await this.view(bound, workspaceId, request.revision);
    } catch (error) {
      throw error instanceof WorkspaceError ? refuse(error) : error;
    }
    const head = await this.headView(bound, workspaceId);
    if (head.revision !== request.baseRevision) {
      // The same restore asked again after its answer was lost: it wrote the revision on top of its base.
      const replay = await this.repository.manifest(
        request.orgId,
        workspaceId,
        request.baseRevision + 1,
      );
      if (
        replay !== null &&
        replay.runId === holder &&
        replay.reason === 'rollback' &&
        replay.contentHash === target.contentHash
      ) {
        return {
          workspaceId,
          revision: replay.revision,
          restoredFrom: request.revision,
          replayed: true,
        };
      }
      throw refuse(
        new WorkspaceError(
          'WORKSPACE_CONFLICT',
          'The workspace changed since this version list was read',
        ),
      );
    }
    const now = this.now();
    const lease = await this.writers.acquire(
      {
        orgId: request.orgId,
        sessionId: request.sessionId,
        workspaceId,
        runId: holder,
        expiresAt: now + RESTORE_LEASE_MS,
      },
      now,
    );
    if (!lease.acquired) {
      throw refuse(
        new WorkspaceError(
          'WORKSPACE_WRITER_BUSY',
          'A task is writing to this workspace',
        ),
      );
    }
    let restored: WorkspaceManifestView;
    try {
      restored = await this.append(bound, head, filesOf(target), 'rollback');
    } catch (error) {
      throw error instanceof WorkspaceError ? refuse(error) : error;
    } finally {
      await this.writers.release(request.orgId, workspaceId, holder);
    }
    this.logger.info(
      'workspace.restored',
      harnessLogMeta('workspace.restored', {
        ...this.fields(bound),
        workspaceId,
        revision: restored.revision,
        toRevision: request.revision,
      }),
    );
    return {
      workspaceId,
      revision: restored.revision,
      restoredFrom: request.revision,
      replayed: false,
    };
  }

  /**
   * A person's files added to the session workspace (SLASH3 `/upload`): one
   * revision with reason `upload` on top of the head they saw, under the writer
   * lease as a restore is (a live writer refuses it; a moved head conflicts).
   * Its holder is the upload's own, so `/undo` takes back exactly it. Asked
   * again after its answer was lost, it answers the revision it wrote.
   */
  async upload(request: UploadWorkspaceFiles): Promise<UploadedWorkspaceFiles> {
    const workspaceId = sessionWorkspaceId(request.sessionId);
    const holder = `${UPLOAD_HOLDER_PREFIX}${createHash('sha256')
      .update(
        [
          request.orgId,
          request.sessionId,
          request.actorId,
          String(request.baseRevision),
          request.idempotencyKey,
        ].join('\0'),
      )
      .digest('hex')
      .slice(0, 32)}`;
    const bound = sessionBound(request, holder);
    const head = await this.headView(bound, workspaceId);
    if (head.revision !== request.baseRevision) {
      const replay = await this.repository.manifest(
        request.orgId,
        workspaceId,
        request.baseRevision + 1,
      );
      if (replay?.runId === holder && replay.reason === 'upload') {
        return { workspaceId, revision: replay.revision, replayed: true };
      }
      throw new WorkspaceError(
        'WORKSPACE_CONFLICT',
        'The workspace changed since its files were read',
      );
    }
    const changes = this.validated(bound, workspaceId, {
      upserts: request.files,
      deletes: [],
    });
    const now = this.now();
    const lease = await this.writers.acquire(
      {
        orgId: request.orgId,
        sessionId: request.sessionId,
        workspaceId,
        runId: holder,
        expiresAt: now + RESTORE_LEASE_MS,
      },
      now,
    );
    if (!lease.acquired) {
      throw new WorkspaceError(
        'WORKSPACE_WRITER_BUSY',
        'A task is writing to this workspace',
      );
    }
    let uploaded: WorkspaceManifestView;
    try {
      uploaded = await this.append(
        bound,
        head,
        this.applied(bound, workspaceId, filesOf(head), changes),
        'upload',
      );
    } finally {
      await this.writers.release(request.orgId, workspaceId, holder);
    }
    return { workspaceId, revision: uploaded.revision, replayed: false };
  }

  /**
   * A session forked into another (SS3): the source workspace's head becomes
   * revision 1 of the new session's workspace, every object copied through
   * `reseal` (the new session's key). Not a P6.4 fork: it records no origin
   * and is never merged back. Null when the source has no files yet; a fork
   * that already wrote its workspace (a resumed request) answers it again.
   */
  async forkSession(
    request: SessionWorkspaceFork,
    reseal: () => Promise<ObjectReseal>,
  ): Promise<ForkedSessionWorkspace | null> {
    const from = sessionWorkspaceId(request.fromSessionId);
    const into = sessionWorkspaceId(request.toSessionId);
    const holder = `${SESSION_FORK_HOLDER_PREFIX}${request.toSessionId}`;
    const existing = await this.repository.head(request.orgId, into);
    if (existing !== null) {
      const first = await this.repository.manifest(request.orgId, into, 1);
      return {
        workspaceId: into,
        revision: existing.revision,
        files: first?.fileCount ?? 0,
        replayed: true,
      };
    }
    const source = await this.headView(
      sessionBound(
        { orgId: request.orgId, sessionId: request.fromSessionId },
        holder,
      ),
      from,
    );
    if (source.revision === 0) return null;
    const bytes = source.files.reduce((sum, file) => sum + file.sizeBytes, 0);
    if (
      source.files.length > MAX_WORKSPACE_FILES ||
      bytes > SESSION_FORK_MAX_WORKSPACE_BYTES
    ) {
      throw new WorkspaceError(
        'WORKSPACE_TOO_LARGE',
        'The workspace is too large to copy into a private conversation',
      );
    }
    const copy = await reseal();
    const files = await mapWithConcurrency(
      source.files,
      SESSION_FORK_OBJECT_CONCURRENCY,
      copy,
    );
    const bound = sessionBound(
      { orgId: request.orgId, sessionId: request.toSessionId },
      holder,
    );
    let forked: WorkspaceManifestView;
    try {
      forked = await this.append(
        bound,
        emptyView(into),
        new Map(files.map((file) => [file.path, file])),
        'fork',
      );
    } catch (error) {
      // A concurrent retry of the same fork wrote it first.
      if (
        error instanceof WorkspaceError &&
        error.code === 'WORKSPACE_CONFLICT'
      ) {
        return this.forkSession(request, reseal);
      }
      throw error;
    }
    this.logger.info(
      'workspace.session_forked',
      harnessLogMeta('workspace.session_forked', {
        orgId: request.orgId,
        sessionId: request.toSessionId,
        fromSessionId: request.fromSessionId,
        actorId: request.actorId,
        fromRevision: source.revision,
        files: files.length,
        bytes,
      }),
    );
    return {
      workspaceId: into,
      revision: forked.revision,
      files: files.length,
      replayed: false,
    };
  }

  /** What a child's merge into its parent's workspace did, from its fork's record (P6.4); null: never merged. */
  async childMerge(
    scope: SessionWorkspaceScope & WorkspacePrincipal,
    childRunId: string,
  ): Promise<ChildMergeReport | null> {
    const forkId = childForkOf(principalWorkspaceId(scope), childRunId);
    const fork = await this.repository.head(scope.orgId, forkId);
    const record = fork?.lastMerge ?? null;
    if (record === null) return null;
    const plan = await this.recordedPlan(
      sessionBound(scope, 'viewer'),
      forkId,
      record,
    );
    return {
      status: record.status,
      into: record.into,
      revision: record.revision,
      forkRevision: record.theirs,
      merged: plan.merged,
      conflicts: plan.conflicts.map((conflict) => ({
        path: conflict.path,
        kind: conflictKind(conflict),
      })),
      repositoryConflicts: plan.repositoryConflicts,
    };
  }

  /** The fork's revision 1 when this exact fork exists; refused if the id is taken otherwise. */
  private async existingFork(
    bound: Bound,
    workspaceId: string,
    request: { atRevision: number; forkId: string },
  ): Promise<WorkspaceManifestView | null> {
    const existing = await this.repository.head(
      bound.run.orgId,
      request.forkId,
    );
    if (existing === null) return null;
    const origin = existing.forkOf;
    if (
      origin?.workspaceId !== workspaceId ||
      origin.revision !== request.atRevision
    ) {
      throw this.forkConflict(
        bound,
        request.forkId,
        origin === null ? 'not_a_fork' : 'other_origin',
      );
    }
    return this.view(bound, request.forkId, 1);
  }

  /** The session's data key, unwrapped for the worker holding this run's lease. */
  async issueKey(attempt: AttemptClaims): Promise<IssuedWorkspaceKey> {
    const bound = await this.bind(attempt, 'write');
    try {
      const issued = await this.keys.issue(
        bound.run.orgId,
        bound.run.sessionId,
      );
      this.logger.info(
        'workspace.key_issued',
        harnessLogMeta('workspace.key_issued', {
          ...this.fields(bound),
          keyId: issued.keyId,
        }),
      );
      return issued;
    } catch (error) {
      if (error instanceof WorkspaceError) {
        this.refused(bound, error.code, {});
      }
      throw error;
    }
  }

  private async bind(
    attempt: AttemptClaims,
    access: Access,
    ...workspaceIds: string[]
  ): Promise<AttemptBound> {
    const run = await this.runs.get(attempt.orgId, attempt.runId);
    if (run === null) throw new HarnessNotFoundError();
    const bound = { run, attempt };
    const allowed =
      access === 'write'
        ? attemptOwnsRun(run, attempt)
        : attemptMayReadRun(run, attempt);
    if (!allowed) {
      this.refused(bound, 'not_owner', {});
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Worker no longer owns this run',
      );
    }
    for (const workspaceId of workspaceIds) {
      if (!workspaceInSession(workspaceId, run.sessionId)) {
        this.refused(bound, 'not_in_session', { workspaceId });
        throw new WorkspaceError(
          'WORKSPACE_FORBIDDEN',
          'The workspace is not this session’s',
        );
      }
      // OWD-16: in a shared conversation a run reaches only its own person's workspaces.
      if (!workspaceOfPrincipal(run, workspaceId)) {
        this.refused(bound, 'other_principal', { workspaceId });
        throw new WorkspaceError(
          'WORKSPACE_FORBIDDEN',
          'The workspace belongs to another person in this conversation',
        );
      }
    }
    return bound;
  }

  /** A child writes only its own fork (P6.4): never its session's workspace or a sibling's fork. */
  private assertWritable(bound: AttemptBound, workspaceId: string): void {
    if (workspaceWritableBy(bound.run, workspaceId)) return;
    this.refused(bound, 'not_writable', { workspaceId });
    throw new WorkspaceError(
      'WORKSPACE_FORBIDDEN',
      'A child run writes only its own workspace fork',
    );
  }

  private async headView(
    bound: Bound,
    workspaceId: string,
  ): Promise<WorkspaceManifestView> {
    const head = await this.repository.head(bound.run.orgId, workspaceId);
    return head === null
      ? emptyView(workspaceId)
      : this.view(bound, workspaceId, head.revision);
  }

  private async view(
    bound: Bound,
    workspaceId: string,
    revision: number,
  ): Promise<WorkspaceManifestView> {
    if (revision === 0) return emptyView(workspaceId);
    const stored =
      revision > 0
        ? await this.repository.manifest(bound.run.orgId, workspaceId, revision)
        : null;
    if (stored === null) throw notFound(workspaceId, revision);
    const files = JSON.parse(
      (
        await this.cipher.open(
          stored.orgId,
          'workspace.manifest',
          manifestSealContext(workspaceId, revision),
          stored.files,
        )
      ).toString('utf8'),
    ) as WorkspaceFileEntry[];
    return {
      workspaceId,
      revision,
      parentRevision: stored.parentRevision,
      contentHash: stored.contentHash,
      files,
      createdAt: stored.createdAt,
    };
  }

  private async append(
    bound: Bound,
    head: WorkspaceManifestView,
    files: WorkspaceFiles,
    reason: StoredManifest['reason'],
    extra: Pick<WorkspaceAppend, 'forkOf' | 'mergedFork'> = {},
  ): Promise<WorkspaceManifestView> {
    const { orgId, runId } = bound.run;
    const revision = head.revision + 1;
    const view: WorkspaceManifestView = {
      workspaceId: head.workspaceId,
      revision,
      parentRevision: head.revision === 0 ? null : head.revision,
      contentHash: manifestContentHash(files),
      files: sortedFiles(files),
      createdAt: this.now(),
    };
    try {
      await this.repository.append({
        ...extra,
        expectedRevision: head.revision,
        manifest: {
          orgId,
          workspaceId: view.workspaceId,
          revision,
          parentRevision: view.parentRevision,
          contentHash: view.contentHash,
          fileCount: view.files.length,
          files: await this.cipher.seal(
            orgId,
            'workspace.manifest',
            manifestSealContext(view.workspaceId, revision),
            Buffer.from(JSON.stringify(view.files), 'utf8'),
          ),
          reason,
          runId,
          createdAt: view.createdAt,
        },
      });
    } catch (error) {
      if (
        error instanceof WorkspaceError &&
        error.code === 'WORKSPACE_CONFLICT'
      ) {
        throw this.conflict(bound, view.workspaceId, head.revision, null);
      }
      throw error;
    }
    this.logger.info(
      'workspace.committed',
      harnessLogMeta('workspace.committed', {
        ...this.fields(bound),
        workspaceId: view.workspaceId,
        revision,
        reason,
        files: view.files.length,
        contentHash: view.contentHash,
      }),
    );
    return view;
  }

  private validated(
    bound: Bound,
    workspaceId: string,
    changes: WorkspaceChanges,
  ): WorkspaceChanges {
    try {
      return validateChanges(changes);
    } catch (error) {
      if (error instanceof WorkspaceError) {
        this.refused(bound, error.code, { workspaceId });
      }
      throw error;
    }
  }

  private applied(
    bound: Bound,
    workspaceId: string,
    files: WorkspaceFiles,
    changes: WorkspaceChanges,
  ): Map<string, WorkspaceFileEntry> {
    try {
      return applyChanges(files, changes);
    } catch (error) {
      if (error instanceof WorkspaceError) {
        this.refused(bound, error.code, { workspaceId });
      }
      throw error;
    }
  }

  private conflict(
    bound: Bound,
    workspaceId: string,
    expected: number,
    actual: number | null,
  ): WorkspaceError {
    this.logger.info(
      'workspace.commit_conflict',
      harnessLogMeta('workspace.commit_conflict', {
        ...this.fields(bound),
        workspaceId,
        expectedRevision: expected,
        revision: actual,
      }),
    );
    return new WorkspaceError(
      'WORKSPACE_CONFLICT',
      'Workspace HEAD moved since the expected revision',
    );
  }

  private forkConflict(
    bound: Bound,
    forkId: string,
    reason: string,
  ): WorkspaceError {
    this.refused(bound, 'WORKSPACE_FORK_CONFLICT', {
      workspaceId: forkId,
      detail: reason,
    });
    return new WorkspaceError(
      'WORKSPACE_FORK_CONFLICT',
      `${forkId} cannot be forked here (${reason})`,
    );
  }

  private fields(bound: Bound): ReturnType<typeof runLogFields> {
    return {
      ...runLogFields(bound.run),
      fence: bound.attempt?.fence,
      ...(bound.attempt !== null ? { fence: bound.attempt.fence } : {}),
    };
  }

  private refused(
    bound: Bound,
    reason: string,
    extra: { workspaceId?: string; detail?: string },
  ): void {
    this.logger.warn(
      'workspace.refused',
      harnessLogMeta('workspace.refused', {
        ...this.fields(bound),
        reason,
        ...extra,
      }),
    );
  }
}
