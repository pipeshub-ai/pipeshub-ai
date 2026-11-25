import { createHash } from 'node:crypto';
import { CommitReceipt, HarnessRun } from '../domain/execution';
import type { RunAdmissionResult } from '../domain/admission';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  SessionErasedError,
} from '../domain/errors';
import { SessionErasureQueue } from '../ports/session-erasure';
import {
  ChatSessionRepository,
  SessionExecutionScope,
  SessionStreamAccess,
} from '../ports/chat-session-repository';
import { ExecutionService } from './execution-service';
import { principalRefFor } from '../domain/run-spec';
import { runViewAudience } from '../domain/session-audience';
import {
  COMPACT_FOCUS_MAX_CHARS,
  CompactRequest,
  CompactRequests,
} from '../domain/compact-requests';
import {
  EffectivePolicy,
  ResolvedRunView,
  UNPINNED_POLICY,
  unpinnedRunView,
  UnpinnedRunView,
} from '../domain/effective-policy';

export interface ControlSessionRun extends SessionExecutionScope {
  runId: string;
  /** Absent: the run as it is now, re-read if a worker commit moves it meanwhile (UX3). */
  expectedRevision?: number;
  idempotencyKey: string;
  action: 'resume' | 'cancel';
}

/** How often a control without a revision re-reads a run a worker keeps moving. */
const UNCONDITIONAL_CONTROL_ATTEMPTS = 3;

export class SessionRunService {
  constructor(
    private readonly executions: ExecutionService,
    private readonly sessions: ChatSessionRepository,
    /** Tells an erased session (410) from one that never was (404); absent: 404 for both. */
    private readonly erasures?: Pick<SessionErasureQueue<unknown>, 'find'>,
    private readonly compacts?: CompactRequests,
    private readonly clock: () => number = Date.now,
  ) {}

  /**
   * The person's `/compact [focus]` (SLASH2 C2): saved on the session for its
   * next run, which compacts before its first turn. The session's owner only,
   * as its other settings are; never sent to a model, and a run already going
   * keeps its context (it waits, O7). Conflict when no harness run was ever
   * admitted here: there is nothing to compact.
   */
  async requestCompact(
    scope: SessionExecutionScope,
    focus: string | undefined,
  ): Promise<CompactRequest> {
    const access = await this.sessions.findStreamAccess(scope);
    if (access === null) throw await this.notFound(scope);
    if (access.audience !== 'owner') throw new HarnessNotFoundError();
    if (this.compacts === undefined) {
      throw new Error('Compact requests are not configured');
    }
    const kept = focus?.trim().slice(0, COMPACT_FOCUS_MAX_CHARS);
    const request: CompactRequest = {
      ...(kept ? { focus: kept } : {}),
      requestedAt: this.clock(),
      requestedBy: principalRefFor(scope.actorId),
    };
    if (
      !(await this.compacts.requestCompact(
        scope.orgId,
        scope.sessionId,
        request,
      ))
    ) {
      throw new HarnessConflictError(
        'NOTHING_TO_COMPACT',
        'This chat has nothing to compact yet',
      );
    }
    return request;
  }

  /**
   * A run its own actor controls: the session's owner, or a collaborator
   * their own run (SS2). Nobody else's run is found.
   */
  async get(scope: SessionExecutionScope, runId: string): Promise<HarnessRun> {
    const access = await this.sessions.findStreamAccess(scope);
    if (access === null) throw await this.notFound(scope);
    const run = await this.executions.get(scope.orgId, runId);
    if (run?.sessionId !== scope.sessionId || run.actorId !== scope.actorId) {
      throw new HarnessNotFoundError();
    }
    return run;
  }

  /** What an authorized run may do (SEC1): ask only with a run `get` returned. */
  async effectivePolicy(run: HarnessRun): Promise<EffectivePolicy> {
    return (
      (await this.executions.effectivePolicy(run.orgId, run.runId)) ??
      UNPINNED_POLICY
    );
  }

  /** The pin of a run `get` returned (CPD-5); an empty view when the control plane did not pin it. */
  async resolved(run: HarnessRun): Promise<ResolvedRunView | UnpinnedRunView> {
    return (
      (await this.executions.resolvedView(run.orgId, run.runId)) ??
      unpinnedRunView()
    );
  }

  /** Throws as `get` does when the viewer may not watch the session at all. */
  /** The session's owner, or not found: SLASH3's sandbox commands are the owner's alone. */
  async assertOwner(scope: SessionExecutionScope): Promise<void> {
    const access = await this.sessions.findStreamAccess(scope);
    if (access === null) throw await this.notFound(scope);
    if (access.audience !== 'owner') throw new HarnessNotFoundError();
  }

  async assertAccess(scope: SessionExecutionScope): Promise<void> {
    if ((await this.sessions.findStreamAccess(scope)) === null)
      throw await this.notFound(scope);
  }

  /**
   * A harness v2 run of this session, and how its viewer may watch it: the
   * run's actor fully, any other participant (the session's owner included)
   * as a shared viewer, whose citations and artifacts are checked (SS2).
   */
  async streamAccess(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<{ run: HarnessRun; access: SessionStreamAccess }> {
    const access = await this.sessions.findStreamAccess(scope);
    if (access === null) throw await this.notFound(scope);
    const run = await this.executions.get(scope.orgId, runId);
    if (run?.sessionId !== scope.sessionId || run.harnessMajor !== 2) {
      throw new HarnessNotFoundError();
    }
    return {
      run,
      access: { ...access, audience: runViewAudience(run, scope.actorId) },
    };
  }

  /**
   * `streamAccess` for the turn that was just admitted. A conversation the
   * admission created is its actor's own and holds only this run, so the
   * stream opens on what the admission wrote, with no second read of the
   * session or the run (WP88-FU-4: two store commands of every new
   * conversation's turn); any other turn is read as `streamAccess` reads it.
   */
  async admittedStreamAccess(
    scope: SessionExecutionScope,
    admitted: Pick<RunAdmissionResult, 'run' | 'created' | 'createdSession'>,
  ): Promise<{ run: HarnessRun; access: SessionStreamAccess }> {
    const { run, created, createdSession } = admitted;
    if (
      created &&
      createdSession !== undefined &&
      run.harnessMajor === 2 &&
      run.sessionId === scope.sessionId &&
      run.actorId === scope.actorId
    ) {
      return {
        run,
        access: {
          audience: runViewAudience(run, scope.actorId),
          title: createdSession.title,
        },
      };
    }
    return this.streamAccess(scope, run.runId);
  }

  /**
   * The run a durable session is running now (`null`: none), for the
   * conversation GET; `undefined` when the session is not one the viewer
   * may watch (legacy sessions keep their response unchanged).
   */
  async activeRunId(
    scope: SessionExecutionScope,
  ): Promise<string | null | undefined> {
    const run = await this.activeRun(scope);
    return run === undefined ? undefined : (run?.runId ?? null);
  }

  /** The run a durable session is running now (`null`: none); `undefined` when the viewer may not watch the session. */
  async activeRun(
    scope: SessionExecutionScope,
  ): Promise<HarnessRun | null | undefined> {
    if ((await this.sessions.findStreamAccess(scope)) === null)
      return undefined;
    return this.executions.getActive(scope.orgId, scope.sessionId);
  }

  async control(command: ControlSessionRun): Promise<CommitReceipt> {
    let run = await this.get(command, command.runId);
    const prefix = this.controlCommandPrefix(command);
    if (command.expectedRevision === undefined) {
      // A repeated Stop would carry the revision its own first send moved on, a different transition.
      const prior = await this.executions.controlReceipt(
        command.orgId,
        command.runId,
        `${prefix}:commit`,
      );
      if (prior !== null) return { ...prior, replayed: true };
    }
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await this.executions.control({
          orgId: command.orgId,
          runId: command.runId,
          expectedRevision: command.expectedRevision ?? run.revision,
          action: command.action,
          by: principalRefFor(command.actorId),
          commandId: `${prefix}:commit`,
          dispatchCommandId: `${prefix}:dispatch`,
        });
      } catch (error) {
        const moved =
          command.expectedRevision === undefined &&
          error instanceof HarnessConflictError &&
          error.code === 'REVISION_CONFLICT' &&
          attempt < UNCONDITIONAL_CONTROL_ATTEMPTS;
        if (!moved) throw error;
        run = await this.get(command, command.runId);
      }
    }
  }

  private async notFound(scope: SessionExecutionScope): Promise<Error> {
    const erased = await this.erasures?.find(scope.orgId, scope.sessionId);
    return erased !== undefined && erased !== null
      ? new SessionErasedError()
      : new HarnessNotFoundError();
  }

  private controlCommandPrefix(command: ControlSessionRun): string {
    const digest = createHash('sha256')
      .update(
        [
          command.orgId,
          command.sessionId,
          command.runId,
          command.actorId,
          command.action,
          command.idempotencyKey,
        ].join('\0'),
      )
      .digest('hex');
    return `user-control:${digest}`;
  }
}
