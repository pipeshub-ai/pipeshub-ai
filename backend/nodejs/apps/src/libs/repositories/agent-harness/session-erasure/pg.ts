import type { Tx } from '../../uow';
import {
  addCounts,
  assertErasureAdvance,
  ERASURE_STEPS,
  ErasureCounts,
  ErasureJob,
  ErasureRequest,
  ErasureStep,
  requestsOrg,
} from '../../../../modules/agent-harness/domain/session-erasure';
import type { HarnessRun } from '../../../../modules/agent-harness/domain/execution';
import type {
  ErasureJobStore,
  SessionRunLookup,
} from '../../../../modules/agent-harness/ports/session-erasure';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';
import type { SessionErasureDocument } from './document';

type StoredJob = Omit<SessionErasureDocument, '_id'>;

const toJob = (doc: StoredJob): ErasureJob => ({
  orgId: doc.orgId,
  sessionId: doc.sessionId,
  reason: doc.reason,
  by: doc.by ?? null,
  status: doc.status,
  step: doc.step ?? null,
  counts: { ...doc.counts },
  attempt: doc.attempt,
  failures: doc.failures,
  requestedAt: doc.requestedAt,
  nextAttemptAt: doc.nextAttemptAt,
  completedAt: doc.completedAt ?? null,
  lastErrorType: doc.lastErrorType ?? null,
});

const REASON = 'harness session erasure';

/** Rows per INSERT: four parameters each, well under PostgreSQL's 65,535 per statement. */
const ENQUEUE_ROWS = 1_000;

const pendingJob = (request: ErasureRequest, now: number): StoredJob => ({
  orgId: request.orgId,
  sessionId: request.sessionId,
  reason: request.reason,
  by: request.by,
  status: 'pending',
  step: ERASURE_STEPS[0],
  counts: {},
  attempt: 0,
  failures: 0,
  requestedAt: now,
  nextAttemptAt: now,
  completedAt: null,
  lastErrorType: null,
  holder: null,
  leaseExpiresAt: null,
});

/**
 * The job's own position, `$1..$4` of the fenced statements (org, session, attempt, step): a write by a holder that
 * lost the job (its attempt or step moved on, or the job completed) matches no row.
 */
const fence = (job: ErasureJob): unknown[] => [
  job.orgId,
  job.sessionId,
  job.attempt,
  job.step,
];

/**
 * Session-erasure jobs on PostgreSQL (`harness.session_erasures`), leased and fenced on database time. The claim is
 * the jobs role's cross-org `FOR UPDATE SKIP LOCKED`, so two sweepers never hold one job; every later write names the
 * attempt and step it claimed in the statement itself.
 */
export class PgErasureJobStore implements ErasureJobStore<Tx> {
  constructor(private readonly time: PgTime = pgServerTime) {}

  async enqueue(request: ErasureRequest, tx?: Tx): Promise<boolean> {
    return (await this.enqueueMany([request], tx)) === 1;
  }

  async enqueueMany(
    requests: readonly ErasureRequest[],
    tx?: Tx,
  ): Promise<number> {
    const orgId = requestsOrg(requests);
    if (orgId === null) return 0;
    return orgOrJobsUnit(tx, orgId, REASON, { write: true }, async (k) => {
      const now = await this.time.now(k);
      const region = rowRegion();
      let created = 0;
      for (let at = 0; at < requests.length; at += ENQUEUE_ROWS) {
        const rows = requests.slice(at, at + ENQUEUE_ROWS);
        const params = rows.flatMap((request) => [
          request.orgId,
          request.sessionId,
          docParam(pendingJob(request, now)),
          region,
        ]);
        const values = rows
          .map((_row, i) => {
            const at = (n: number): string => `$${String(4 * i + n)}`;
            return `(${at(1)}, ${at(2)}, ${at(3)}::jsonb, ${at(4)})`;
          })
          .join(', ');
        // A repeated delete or a retention pass over a session that already has its erasure changes nothing.
        const { rows: inserted } = await k.query(
          `/* pipeshub:harness.session_erasures_enqueue */ INSERT INTO harness.session_erasures
             (org_id, session_id, doc, region) VALUES ${values}
           ON CONFLICT (org_id, session_id) DO NOTHING RETURNING 1 AS created`,
          params,
        );
        created += inserted.length;
      }
      return created;
    });
  }

  find(orgId: string, sessionId: string): Promise<ErasureJob | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.session_erasures_find */ SELECT doc FROM harness.session_erasures
            WHERE org_id = $1 AND session_id = $2`,
          [orgId, sessionId],
        );
        return rows[0] === undefined
          ? null
          : toJob(decodeDoc<StoredJob>(rows[0].doc));
      },
    );
  }

  claimDue(holder: string, leaseMs: number): Promise<ErasureJob | null> {
    return jobsUnit(REASON, { write: true }, async (k) => {
      const now = await this.time.now(k);
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.session_erasures_claim */ WITH due AS (
           SELECT org_id, session_id FROM harness.session_erasures
            WHERE status = 'pending' AND next_attempt_at_ms <= $1
              AND (lease_expires_at_ms IS NULL OR lease_expires_at_ms <= $1)
            ORDER BY next_attempt_at_ms
            LIMIT 1
            FOR UPDATE SKIP LOCKED
         )
         UPDATE harness.session_erasures e
            SET doc = e.doc || jsonb_build_object(
              'attempt', (e.doc->>'attempt')::int + 1,
              'holder', $2::text,
              'leaseExpiresAt', $1::bigint + $3::bigint)
           FROM due
          WHERE e.org_id = due.org_id AND e.session_id = due.session_id
          RETURNING e.doc`,
        [now, holder, leaseMs],
      );
      return rows[0] === undefined
        ? null
        : toJob(decodeDoc<StoredJob>(rows[0].doc));
    });
  }

  async advance(
    job: ErasureJob,
    counts: ErasureCounts,
    next: ErasureStep | null,
    leaseMs: number,
    tx?: Tx,
  ): Promise<boolean> {
    assertErasureAdvance(job, next);
    return await orgOrJobsUnit(
      tx,
      job.orgId,
      REASON,
      { write: true },
      async (k) => {
        const now = await this.time.now(k);
        const patch = {
          step: next,
          status: next === null ? 'completed' : 'pending',
          counts: addCounts(job.counts, counts),
          failures: 0,
          lastErrorType: null,
          completedAt: next === null ? now : null,
          ...(next === null
            ? { holder: null, leaseExpiresAt: null }
            : { leaseExpiresAt: now + leaseMs }),
        };
        return this.patch(k, job, patch, false);
      },
    );
  }

  reschedule(
    job: ErasureJob,
    at: number,
    failure: { errorType: string } | null,
  ): Promise<boolean> {
    return orgOrJobsUnit(undefined, job.orgId, REASON, { write: true }, (k) =>
      this.patch(
        k,
        job,
        {
          nextAttemptAt: at,
          holder: null,
          leaseExpiresAt: null,
          ...(failure !== null ? { lastErrorType: failure.errorType } : {}),
        },
        failure !== null,
      ),
    );
  }

  withdraw(job: ErasureJob): Promise<boolean> {
    return orgOrJobsUnit(
      undefined,
      job.orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.session_erasures_withdraw */ DELETE FROM harness.session_erasures
            WHERE org_id = $1 AND session_id = $2 AND status = 'pending'
            AND (doc->>'attempt')::int = $3 AND (doc->>'step') IS NOT DISTINCT FROM $4`,
          fence(job),
        );
        return rowCount === 1;
      },
    );
  }

  findMany(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, ErasureJob>> {
    if (sessionIds.length === 0)
      return Promise.resolve(new Map<string, ErasureJob>());
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.session_erasures_find_many */ SELECT doc FROM harness.session_erasures
            WHERE org_id = $1 AND session_id = ANY($2::text[])`,
          [orgId, [...sessionIds]],
        );
        const jobs = rows.map((row) => toJob(decodeDoc<StoredJob>(row.doc)));
        return new Map(jobs.map((job) => [job.sessionId, job]));
      },
    );
  }

  now(): Promise<number> {
    return jobsUnit(REASON, { write: false }, (k) => this.time.now(k));
  }

  /** Merges `fields` into the job's document (Mongo's `$set` on top-level fields) under the fence. */
  private async patch(
    k: PgKernelTx,
    job: ErasureJob,
    fields: Record<string, unknown>,
    countFailure: boolean,
  ): Promise<boolean> {
    const { rowCount } = await k.query(
      `/* pipeshub:harness.session_erasures_patch */ UPDATE harness.session_erasures
          SET doc = doc || $5::jsonb || CASE WHEN $6::boolean
            THEN jsonb_build_object('failures', (doc->>'failures')::int + 1) ELSE '{}'::jsonb END
          WHERE org_id = $1 AND session_id = $2 AND status = 'pending'
            AND (doc->>'attempt')::int = $3 AND (doc->>'step') IS NOT DISTINCT FROM $4`,
      [...fence(job), docParam(fields), countFailure],
    );
    return rowCount === 1;
  }
}

type RunSummary = Pick<
  HarnessRun,
  'runId' | 'status' | 'revision' | 'harnessMajor'
>;

/** A session's runs on PostgreSQL (`harness.runs`, the execution store's), as erasure and the views read them. */
export class PgSessionRunLookup implements SessionRunLookup {
  runsOf(orgId: string, sessionId: string): Promise<RunSummary[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: Record<string, unknown> }>(
          `/* pipeshub:harness.runs_of_session */ SELECT jsonb_strip_nulls(jsonb_build_object(
               'runId', doc->'runId', 'status', doc->'status', 'revision', doc->'revision',
               'harnessMajor', doc->'harnessMajor')) AS doc
             FROM harness.runs WHERE org_id = $1 AND session_id = $2 ORDER BY run_id`,
          [orgId, sessionId],
        );
        return rows.map((row) => decodeDoc<RunSummary>(row.doc));
      },
    );
  }

  /** The root harness-v2 runs `actorId` started in the session (SLASH2 `/usage`). */
  ownRootRunsOf(
    orgId: string,
    sessionId: string,
    actorId: string,
  ): Promise<string[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ run_id: string }>(
          `/* pipeshub:harness.runs_own_roots */ SELECT run_id FROM harness.runs
            WHERE org_id = $1 AND session_id = $2 AND actor_id = $3 AND harness_major = 2
              AND parent_run_id IS NULL ORDER BY run_id`,
          [orgId, sessionId, actorId],
        );
        return rows.map((row) => row.run_id);
      },
    );
  }
}
