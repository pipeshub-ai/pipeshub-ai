import { HarnessConflictError } from '../domain/errors';
import { TERMINAL_RUN_STATUSES } from '../domain/execution';
import {
  ORG_SESSIONS_PER_PASS,
  ORG_SESSIONS_POLL_MS,
  type OrgErasureStep,
  type PrincipalErasureJob,
} from '../domain/principal-erasure';
import { DAY_MS } from '../domain/session-retention';
import type {
  OrgErasureParts,
  OrgErasureSteps,
  PrincipalErasureOutcome,
} from './principal-erasure';

type Outcome = PrincipalErasureOutcome;
type Step = (job: PrincipalErasureJob, now: number) => Promise<Outcome>;

/** Query-service pages one pass of `query_service` asks for before it yields. */
const QUERY_SERVICE_PAGES = 20;

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

/** When an org's audit chain may go: its retention period after its data keys were destroyed. */
export const auditRemovableAt = (now: number, retentionDays: number): number =>
  now + Math.max(0, retentionDays) * DAY_MS;

/**
 * The built-in steps of an org's erasure job, run once the org is deleted:
 * its runs cancelled and sandboxes ended, then every session (each through
 * its own session erasure job: journal, payloads, workspaces, artifacts), the
 * query service's and the sandboxes' data of the org, its principal stores,
 * its documents and Redis keys, its data keys, and its audit chain after the
 * job's audit retention (none unless the request set one). The deletion
 * request has already announced the org's deletion, so `org_event` does
 * nothing here. Every step is idempotent; the shared runner moves the job on.
 * `steps` lets an extension replace single steps and keep the rest.
 */
export class ImmediateOrgErasureSteps<Tx> implements OrgErasureSteps {
  protected readonly steps: Record<OrgErasureStep, Step>;

  constructor(protected readonly parts: OrgErasureParts<Tx>) {
    this.steps = {
      cancel_runs: (job, now) => this.cancelRuns(job, now),
      end_sandboxes: (job) => this.endSandboxes(job),
      org_event: () => Promise.resolve({ kind: 'done' }),
      sessions: (job, now) => this.enqueueSessions(job, now),
      await_sessions: async (job, now) =>
        (await parts.org.pendingSessionErasures(job.orgId)) > 0
          ? { kind: 'wait', at: now + ORG_SESSIONS_POLL_MS }
          : { kind: 'done' },
      query_service: (job, now) => this.queryService(job, now),
      sandbox_folders: async (job) => {
        if (parts.sandboxes === null) return { kind: 'done' };
        const erased = await parts.sandboxes.eraseOrg(job.orgId);
        return {
          kind: 'done',
          counts: {
            sandboxes: erased.sandboxes,
            sandboxFolders: erased.folders,
          },
        };
      },
      principals: async (job) => {
        const counts: Record<string, number> = {};
        for (const eraser of parts.principals) {
          const erased = await eraser.eraseOrg(job.orgId);
          counts[eraser.store] = typeof erased === 'number' ? erased : 0;
        }
        return { kind: 'done', counts };
      },
      org_stores: async (job) => ({
        kind: 'done',
        counts: await parts.org.eraseStores(job.orgId),
      }),
      redis: async (job) => ({
        kind: 'done',
        counts: { redisKeys: (await parts.redis?.eraseOrg(job.orgId)) ?? 0 },
      }),
      data_keys: async (job, now) => ({
        kind: 'done',
        counts: { dataKeys: await parts.org.destroyDataKeys(job.orgId) },
        patch: {
          auditRemoveAfter: auditRemovableAt(now, job.auditRetentionDays),
        },
      }),
      audit_retention: (job, now) =>
        Promise.resolve(
          job.auditRemoveAfter !== null && now < job.auditRemoveAfter
            ? { kind: 'wait', at: job.auditRemoveAfter }
            : { kind: 'done' },
        ),
      audit: async (job) => ({
        kind: 'done',
        counts: { auditRecords: await parts.org.eraseAudit(job.orgId) },
      }),
    };
  }

  run(job: PrincipalErasureJob, now: number): Promise<Outcome> {
    const step = this.steps[job.step as OrgErasureStep] as Step | undefined;
    if (step === undefined) {
      throw new HarnessConflictError(
        'INVALID_TRANSITION',
        `An org erasure has no step ${job.step}`,
      );
    }
    return step(job, now);
  }

  private async cancelRuns(
    job: PrincipalErasureJob,
    now: number,
  ): Promise<Outcome> {
    let cancelled = 0;
    for (const run of await this.parts.org.activeRuns(job.orgId)) {
      if (TERMINAL_RUN_STATUSES.has(run.status) || run.status === 'cancelling')
        continue;
      const commandId = `org-erase:${job.orgId}:${run.runId}`;
      try {
        await this.parts.executions.control({
          orgId: job.orgId,
          runId: run.runId,
          expectedRevision: run.revision,
          action: 'cancel',
          by: job.by,
          commandId,
          dispatchCommandId: `${commandId}:dispatch`,
        });
        cancelled += 1;
      } catch (error) {
        // The run moved between the read and the cancel: read it again.
        if (error instanceof HarnessConflictError)
          return { kind: 'wait', at: now + 1_000 };
        throw error;
      }
    }
    return { kind: 'done', counts: { cancelledRuns: cancelled } };
  }

  private async endSandboxes(job: PrincipalErasureJob): Promise<Outcome> {
    if (this.parts.reaper === null) return { kind: 'done' };
    return {
      kind: 'done',
      counts: {
        reapedSandboxes: await this.parts.reaper.reap({ orgId: job.orgId }),
      },
    };
  }

  private async enqueueSessions(
    job: PrincipalErasureJob,
    now: number,
  ): Promise<Outcome> {
    const ids = await this.parts.org.sessionsAfter(
      job.orgId,
      job.cursor,
      ORG_SESSIONS_PER_PASS,
    );
    if (ids.length === 0) return { kind: 'done' };
    await this.parts.transaction(async (tx) => {
      await this.parts.org.markSessionsDeleted(job.orgId, ids, tx);
      for (const sessionId of ids) {
        await this.parts.sessions.enqueue(
          { orgId: job.orgId, sessionId, reason: 'user_delete', by: job.by },
          tx,
        );
      }
    });
    return {
      kind: 'wait',
      at: now,
      patch: {
        cursor: ids[ids.length - 1] ?? job.cursor,
        counts: add(job.counts, { sessions: ids.length }),
      },
    };
  }

  private async queryService(
    job: PrincipalErasureJob,
    now: number,
  ): Promise<Outcome> {
    const service = this.parts.queryService;
    if (service === null) return { kind: 'done' };
    let counts: Record<string, number> = {};
    for (let page = 0; page < QUERY_SERVICE_PAGES; page += 1) {
      const erased = await service.eraseOrg(job.orgId);
      counts = add(counts, {
        attachments: erased.attachments,
        artifacts: erased.artifacts,
        blobs: erased.blobs,
        skillCandidates: erased.skillCandidates,
      });
      if (!erased.remaining) return { kind: 'done', counts };
    }
    return {
      kind: 'wait',
      at: now,
      patch: { counts: add(job.counts, counts) },
    };
  }
}
