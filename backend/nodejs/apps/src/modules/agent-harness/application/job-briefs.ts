import type { JobBriefHistory } from '../contracts/generated/ext/pipeshub/api/job-brief-history';
import type { RequestJobs } from '../contracts/generated/ext/pipeshub/api/request-jobs';
import {
  HarnessNotFoundError,
  HarnessUnavailableError,
} from '../domain/errors';
import { HarnessRun, TERMINAL_RUN_STATUSES } from '../domain/execution';
import {
  delegatedTask,
  jobBrief,
  JobBrief,
  jobTitle,
} from '../domain/job-brief';
import {
  type ListedJob,
  MAX_SESSION_JOBS,
  runListState,
} from '../domain/run-lists';
import { readPinnedSpec } from '../domain/run-spec';
import {
  type JobGoalChange,
  sortChanges,
} from '../projection/job-brief-history';
import type { SessionExecutionScope } from '../ports/chat-session-repository';
import type { ExecutionStore } from '../ports/execution-store';
import type { RunLists } from '../ports/run-lists';
import type { ExecutionService } from './execution-service';
import type { RunInputSeal } from './run-input-seal';
import type { RunTimelineService } from './run-timeline-service';
import type { SessionRunService } from './session-run-service';

export interface JobBriefServiceDeps {
  runs: Pick<SessionRunService, 'streamAccess'>;
  /** The runs with their input opened (a sealed query read back). */
  executions: Pick<ExecutionService, 'get'>;
  /** The pinned specs admission fixed. */
  subjects: Pick<ExecutionStore, 'readClaimSubject'>;
  /** UI-B5: the conversation's jobs, for a request's view and a job's re-plans; absent: those reads are not found. */
  jobs?: Pick<RunLists, 'sessionJobs'>;
  /** Opens the goals of the jobs `jobs` lists. */
  inputs?: Pick<RunInputSeal, 'open'>;
  /** A job's own journal, folded into the messages and choices that changed its goal. */
  timelines?: Pick<RunTimelineService, 'goalChanges'>;
}

/** Most changes a brief's history carries (its oldest). */
const MAX_CHANGES = 200;

/**
 * ADR-UI-01 D3 (G4): a job's brief, for its conversation's owner only (the same reader as its timeline): what it
 * was told and sent, what it was not given and why, and how far it reaches. A root run is no job: not found.
 * D5, D6 (UI-B5): how its goal changed since, and a request's own view of the jobs it handed out.
 */
export class JobBriefService {
  constructor(private readonly deps: JobBriefServiceDeps) {}

  async brief(scope: SessionExecutionScope, runId: string): Promise<JobBrief> {
    const run = await this.ownersJob(scope, runId);
    const [child, subject, parent, parentSubject] = await Promise.all([
      this.deps.executions.get(run.orgId, run.runId),
      this.deps.subjects.readClaimSubject(run.orgId, run.runId),
      this.deps.executions.get(run.orgId, run.parent.runId),
      this.deps.subjects.readClaimSubject(run.orgId, run.parent.runId),
    ]);
    if (child === null || subject?.pinned == null) {
      throw new HarnessNotFoundError();
    }
    return jobBrief(
      child,
      readPinnedSpec(subject.pinned.spec),
      parentSubject?.pinned != null
        ? readPinnedSpec(parentSubject.pinned.spec)
        : null,
      parent?.input ?? null,
    );
  }

  /**
   * D5: how a job's goal changed since it started: the messages added to it and the choices its person made when
   * it asked first (its own journal), and each later job of the conversation that continues it (the coordinator
   * re-planned it), oldest first.
   */
  async history(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<JobBriefHistory> {
    const run = await this.ownersJob(scope, runId);
    const { jobs, inputs, timelines } = this.reads();
    const [child, own, listed] = await Promise.all([
      this.deps.executions.get(run.orgId, run.runId),
      timelines.goalChanges(run),
      jobs.sessionJobs(run.orgId, run.sessionId, MAX_SESSION_JOBS),
    ]);
    const replanned: JobGoalChange[] = await Promise.all(
      listed
        .filter((job) => job.continuesRunId === run.runId)
        .map(async (job) => ({
          kind: 'replanned' as const,
          at: job.run.createdAt,
          runId: job.run.runId,
          goal: (await this.goal(inputs, job)) ?? null,
        })),
    );
    return {
      runId: run.runId,
      startedAs: nonEmpty(delegatedTask(child?.input?.query)),
      changes: sortChanges([...own, ...replanned]).slice(0, MAX_CHANGES),
    };
  }

  /**
   * D6: one request (any run of the conversation) and the jobs it handed out, oldest first, with how many are back
   * (finished, failed or stopped). Its conversation's owner only; a request that handed out none lists none.
   */
  async requestJobs(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<RequestJobs> {
    const { run, access } = await this.deps.runs.streamAccess(scope, runId);
    if (access.audience !== 'owner') throw new HarnessNotFoundError();
    const { jobs, inputs } = this.reads();
    const [request, listed] = await Promise.all([
      this.deps.executions.get(run.orgId, run.runId),
      jobs.sessionJobs(run.orgId, run.sessionId, MAX_SESSION_JOBS),
    ]);
    const own = listed
      .filter((job) => job.run.parent?.runId === run.runId)
      .reverse();
    const views = await Promise.all(
      own.map(async (job) => {
        const ended = TERMINAL_RUN_STATUSES.has(job.run.status);
        return {
          runId: job.run.runId,
          title: jobTitle(await this.goal(inputs, job)) ?? null,
          state: runListState(job.run),
          status: job.run.status,
          engine: job.engine,
          createdAt: job.run.createdAt,
          endedAt: ended ? job.run.updatedAt : null,
          continuesRunId: job.continuesRunId,
        };
      }),
    );
    return {
      request: {
        runId: run.runId,
        sessionId: run.sessionId,
        state: runListState(run),
        status: run.status,
        ask: nonEmpty(request?.input?.query),
      },
      jobs: views,
      total: views.length,
      back: views.filter((job) => job.endedAt !== null).length,
    };
  }

  /** A job of a conversation the caller owns; anything else (a root run, a shared reader) is not found. */
  private async ownersJob(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<HarnessRun & { parent: NonNullable<HarnessRun['parent']> }> {
    const { run, access } = await this.deps.runs.streamAccess(scope, runId);
    const { parent } = run;
    if (access.audience !== 'owner' || parent === undefined) {
      throw new HarnessNotFoundError();
    }
    return { ...run, parent };
  }

  private reads(): Required<
    Pick<JobBriefServiceDeps, 'jobs' | 'inputs' | 'timelines'>
  > {
    const { jobs, inputs, timelines } = this.deps;
    if (jobs === undefined || inputs === undefined || timelines === undefined) {
      throw new HarnessUnavailableError(
        'Job history is not available on this deployment',
      );
    }
    return { jobs, inputs, timelines };
  }

  /** A listed job's goal in plaintext; absent once erased, or when its seal cannot be opened (never fails a list). */
  private async goal(
    inputs: Pick<RunInputSeal, 'open'>,
    job: ListedJob,
  ): Promise<string | undefined> {
    try {
      return (
        nonEmpty(delegatedTask((await inputs.open(job.run)).input?.query)) ??
        undefined
      );
    } catch {
      return undefined;
    }
  }
}

/** Longest goal or ask a view carries (code points), as the contracts bound them. */
const MAX_TEXT = 10_000;

const nonEmpty = (text: string | null | undefined): string | null =>
  typeof text === 'string' && text.trim().length > 0
    ? Array.from(text).slice(0, MAX_TEXT).join('')
    : null;
