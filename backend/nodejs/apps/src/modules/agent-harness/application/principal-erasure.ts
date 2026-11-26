import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import type { CostEraser } from '../ports/cost-ledger';
import { HarnessConflictError } from '../domain/errors';
import { DAY_MS } from '../domain/session-retention';
import {
  nextPrincipalStep,
  PRINCIPAL_ERASURES_PER_TICK,
  PrincipalErasureJob,
  UserErasureStep,
} from '../domain/principal-erasure';
import {
  ATTACHMENT_ERASE_PAGE,
  erasureBackoffMs,
} from '../domain/session-erasure';
import {
  OrgDataStore,
  OrgDeletedAnnouncer,
  OrgQueryServiceEraser,
  OrgRedisEraser,
  PrincipalErasureJobStore,
  SandboxOrgEraser,
  SessionOwnerRecorder,
  UserControlPlaneEraser,
} from '../ports/principal-erasure';
import { SandboxReaper } from '../ports/sandbox-reaper';
import {
  AttachmentEraser,
  SessionErasureQueue,
  UnsentAttachments,
} from '../ports/session-erasure';
import type { CodingEnginePreferences } from '../ports/coding-engines';
import { ExecutionService } from './execution-service';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/** One principal store's hook for a deleted org (C-14 PRINCIPAL_DATA_STORES). */
export interface OrgPrincipalEraser {
  store: string;
  /** How many items it erased, where the store counts them. */
  eraseOrg(orgId: string): Promise<unknown>;
}

/** What one step of a job answers: done (the job moves on), or wait until `at`. */
export type PrincipalErasureOutcome =
  | {
      kind: 'done';
      counts?: Record<string, number>;
      patch?: Partial<PrincipalErasureJob>;
    }
  | { kind: 'wait'; at: number; patch?: Partial<PrincipalErasureJob> };

/**
 * The steps of an org's erasure job (`kind: 'org'`): the built-in ones
 * (`ImmediateOrgErasureSteps`) unless an extension supplies its own through
 * `HarnessEdition.orgErasure`.
 */
export interface OrgErasureSteps {
  /** Runs the job's current step; idempotent, as every step is. */
  run(job: PrincipalErasureJob, now: number): Promise<PrincipalErasureOutcome>;
}

/** What an org's erasure steps are built from: the org's stores and the services that erase it. */
export interface OrgErasureParts<Tx> {
  jobs: PrincipalErasureJobStore<Tx>;
  org: OrgDataStore<Tx>;
  sessions: SessionErasureQueue<Tx>;
  executions: Pick<ExecutionService, 'control'>;
  reaper: SandboxReaper | null;
  sandboxes: SandboxOrgEraser | null;
  queryService: OrgQueryServiceEraser | null;
  principals: readonly OrgPrincipalEraser[];
  redis: OrgRedisEraser | null;
  announcer: OrgDeletedAnnouncer | null;
  transaction: <T>(work: (tx: Tx) => Promise<T>) => Promise<T>;
  logger: HarnessLogger;
}

/** How long an org job waits, where no org steps are bound, before it is looked at again. */
export const ORG_JOB_WITHOUT_STEPS_WAIT_MS = DAY_MS;

export interface PrincipalErasureDependencies<Tx> {
  jobs: PrincipalErasureJobStore<Tx>;
  /** Null: org jobs wait, as no step of theirs runs where this runner is built. */
  orgSteps: OrgErasureSteps | null;
  recorder: SessionOwnerRecorder<Tx> | null;
  unsent: { uploads: UnsentAttachments; eraser: AttachmentEraser } | null;
  /** Absent: no control plane in this deployment, so the user owns nothing there. */
  controlPlane?: UserControlPlaneEraser | null;
  /** A deleted person's spend rows; absent: none are kept here. */
  costs?: Pick<CostEraser, 'eraseUser'> | null;
  /** The person's default-chat coding engine (ENGPICK); absent: no coding engines here. */
  engineChoices?: Pick<CodingEnginePreferences, 'eraseUser'> | null;
  transaction: <T>(work: (tx: Tx) => Promise<T>) => Promise<T>;
  logger?: HarnessLogger;
}

type Outcome = PrincipalErasureOutcome;

/** Unsent-upload pages one pass reads. */
const UNSENT_PAGES = 20;

const errorType = (error: unknown): string =>
  error instanceof Error ? error.name : typeof error;

const add = (
  into: Record<string, number>,
  more: Record<string, number> | undefined,
): Record<string, number> => {
  const sum = { ...into };
  for (const [name, value] of Object.entries(more ?? {})) {
    sum[name] = (sum[name] ?? 0) + value;
  }
  return sum;
};

/**
 * Org and user deletions after their request (CLEANUP-G1, G2), one step at a
 * time: a user's steps are built in, an org's come from `orgSteps`. Every step is idempotent and the job's step is written only by the
 * step it was read at, so a crash anywhere resumes at the step it was in and a
 * job removed meanwhile wins over a pass in flight. No network call
 * runs inside a transaction.
 */
export class PrincipalErasureRunner<Tx> {
  private readonly logger: HarnessLogger;

  constructor(private readonly deps: PrincipalErasureDependencies<Tx>) {
    this.logger = deps.logger ?? silentHarnessLogger;
  }

  /** The due jobs, each run until it waits; how many steps finished. */
  async tick(now: number): Promise<number> {
    let finished = 0;
    for (const job of await this.deps.jobs.due(
      now,
      PRINCIPAL_ERASURES_PER_TICK,
    )) {
      // The job row names the org it erases from; each job runs as that org.
      finished += await forEnumeratedOrg(
        job.orgId,
        'agent-harness.principal-erasure',
        () => this.run(job, now),
      );
    }
    return finished;
  }

  async run(start: PrincipalErasureJob, now: number): Promise<number> {
    let job = start;
    let finished = 0;
    for (;;) {
      const step = job.step;
      let outcome: Outcome;
      try {
        outcome = await this.step(job, now);
      } catch (error) {
        const failures = job.failures + 1;
        const failed = {
          ...job,
          failures,
          nextAttemptAt: now + erasureBackoffMs(failures),
          lastErrorType: errorType(error),
        };
        await this.deps.jobs.writeProgress(failed, step);
        this.logger.warn(
          'principal_erasure.retry',
          harnessLogMeta('principal_erasure.retry', {
            ...this.ids(job),
            step,
            failures,
            errorType: errorType(error),
          }),
        );
        return finished;
      }
      if (outcome.kind === 'wait') {
        await this.deps.jobs.writeProgress(
          { ...job, ...outcome.patch, failures: 0, nextAttemptAt: outcome.at },
          step,
        );
        return finished;
      }
      finished += 1;
      const next = nextPrincipalStep(job);
      const counts = add(job.counts, outcome.counts);
      this.logger.info(
        'principal_erasure.step_done',
        harnessLogMeta('principal_erasure.step_done', {
          ...this.ids(job),
          step,
          ...outcome.counts,
        }),
      );
      if (next === null) {
        await this.deps.jobs.remove(job);
        this.logger.info(
          'principal_erasure.completed',
          harnessLogMeta('principal_erasure.completed', {
            ...this.ids(job),
            ...counts,
          }),
        );
        return finished;
      }
      const advanced: PrincipalErasureJob = {
        ...job,
        ...outcome.patch,
        step: next,
        counts,
        failures: 0,
        nextAttemptAt: now,
        lastErrorType: null,
      };
      if (!(await this.deps.jobs.writeProgress(advanced, step)))
        return finished;
      job = advanced;
    }
  }

  private ids(job: PrincipalErasureJob): {
    kind: string;
    orgId: string;
    userId: string | null;
  } {
    return { kind: job.kind, orgId: job.orgId, userId: job.userId };
  }

  private step(job: PrincipalErasureJob, now: number): Promise<Outcome> {
    if (job.kind === 'org') return this.orgStep(job, now);
    const steps: Record<
      UserErasureStep,
      (job: PrincipalErasureJob) => Promise<Outcome>
    > = {
      journal_transfers: (j) => this.journalTransfers(j),
      unsent_uploads: (j) => this.unsentUploads(j, now),
      control_plane: (j) => this.controlPlane(j),
      cost_ledger: async (j) => {
        const costs = this.deps.costs;
        if (costs === undefined || costs === null || j.userId === null) {
          return { kind: 'done' };
        }
        return {
          kind: 'done',
          counts: { costRows: await costs.eraseUser(j.orgId, j.userId) },
        };
      },
      engine_choices: async (j) => {
        const choices = this.deps.engineChoices;
        if (choices === undefined || choices === null || j.userId === null) {
          return { kind: 'done' };
        }
        return {
          kind: 'done',
          counts: { engineChoices: await choices.eraseUser(j.orgId, j.userId) },
        };
      },
    };
    // The step comes from the stored job, which may name one this build does not know.
    const run = (steps as Partial<typeof steps>)[job.step as UserErasureStep];
    if (run === undefined) {
      throw new HarnessConflictError(
        'INVALID_TRANSITION',
        `A user erasure has no step ${job.step}`,
      );
    }
    return run(job);
  }

  /** An org job runs its org steps; with none bound, it is left as it is and looked at again later. */
  private orgStep(job: PrincipalErasureJob, now: number): Promise<Outcome> {
    if (this.deps.orgSteps !== null) return this.deps.orgSteps.run(job, now);
    this.logger.warn(
      'principal_erasure.no_org_steps',
      harnessLogMeta('principal_erasure.no_org_steps', {
        ...this.ids(job),
        step: job.step,
      }),
    );
    return Promise.resolve({
      kind: 'wait',
      at: now + ORG_JOB_WITHOUT_STEPS_WAIT_MS,
    });
  }

  private async journalTransfers(job: PrincipalErasureJob): Promise<Outcome> {
    const recorder = this.deps.recorder;
    const userId = job.userId;
    if (recorder === null || userId === null) return { kind: 'done' };
    let current = job;
    for (
      let index = current.journaled;
      index < current.transfers.length;
      index += 1
    ) {
      const transfer = current.transfers[index];
      if (transfer === undefined) break;
      const step = current.step;
      const next = { ...current, journaled: index + 1 };
      // The event and the progress commit together, so a crash journals each move once.
      const saved = await this.deps.transaction(async (tx) => {
        await recorder.ownerChanged(
          job.orgId,
          transfer.sessionId,
          { by: job.by, fromUserId: userId, toUserId: transfer.to },
          tx,
        );
        if (!(await this.deps.jobs.writeProgress(next, step, tx))) {
          throw new HarnessConflictError(
            'INVALID_TRANSITION',
            'The user erasure moved on',
          );
        }
        return true;
      });
      if (saved) current = next;
    }
    return {
      kind: 'done',
      counts: { ownersChanged: current.transfers.length },
      patch: { journaled: current.journaled },
    };
  }

  private async controlPlane(job: PrincipalErasureJob): Promise<Outcome> {
    const eraser = this.deps.controlPlane;
    if (eraser === undefined || eraser === null || job.userId === null) {
      return { kind: 'done' };
    }
    const erased = await eraser.eraseUser(job.orgId, job.userId);
    return {
      kind: 'done',
      counts: {
        cpInstallations: erased.installations,
        cpBindings: erased.bindings,
        cpGrants: erased.grants,
      },
    };
  }

  private async unsentUploads(
    job: PrincipalErasureJob,
    now: number,
  ): Promise<Outcome> {
    const unsent = this.deps.unsent;
    if (unsent === null || job.userId === null) return { kind: 'done' };
    let after: string | null = null;
    let erased = 0;
    for (let page = 0; page < UNSENT_PAGES; page += 1) {
      const { uploads, remaining, next } = await unsent.uploads.unsent(
        job.orgId,
        now + 1,
        ATTACHMENT_ERASE_PAGE,
        after,
      );
      const theirs = uploads.filter((upload) => upload.ownerId === job.userId);
      if (theirs.length > 0) {
        erased += (
          await unsent.eraser.eraseAttachments(
            job.orgId,
            theirs.map(({ recordId, ownerId }) => ({ recordId, ownerId })),
          )
        ).attachments;
      }
      if (!remaining || next === null) break;
      after = next;
    }
    return { kind: 'done', counts: { unsentUploads: erased } };
  }
}
