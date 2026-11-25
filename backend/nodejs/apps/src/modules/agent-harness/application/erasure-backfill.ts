import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import {
  addBackfillCounts,
  BACKFILL_BATCH_SIZE,
  BACKFILL_DEFAULT_WAIT_MS,
  BACKFILL_LEASE_MS,
  BACKFILL_LEASE_NAME,
  BACKFILL_POLL_MS,
  BACKFILL_SCAN_PAGE,
  BackfillCheckpoint,
  BackfillCounts,
  BackfillCursor,
  BackfillDigester,
  BackfillItem,
  BackfillItemState,
  BackfillMarker,
  BackfillOrgSummary,
  BackfillPlan,
  BackfillRefused,
  backfillBatchAuditEntry,
  backfillCompletedAuditEntry,
  backfillFingerprint,
  INITIAL_BACKFILL_CURSOR,
  isBackfillCandidate,
  zeroBackfillCounts,
} from '../domain/erasure-backfill';
import { ErasureJob } from '../domain/session-erasure';
import { AuditChainStore } from '../ports/audit';
import {
  BackfillSource,
  ErasureBackfillStore,
} from '../ports/erasure-backfill';
import { LeaderLease } from '../ports/leader-lease';
import { ErasureJobStore } from '../ports/session-erasure';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

const BACKFILL_JOB = 'agent-harness.erasure-backfill';

export interface ErasureBackfillDependencies<Tx> {
  source: BackfillSource;
  store: ErasureBackfillStore<Tx>;
  jobs: Pick<ErasureJobStore<Tx>, 'enqueueMany' | 'findMany'>;
  audit: Pick<AuditChainStore<Tx>, 'append'>;
  /** One durable transaction for `orgId`'s batch (PostgreSQL runs it as that org; MongoDB needs no org). */
  transaction: <T>(work: (tx: Tx) => Promise<T>, orgId: string) => Promise<T>;
  lease: LeaderLease;
  holderId: string;
  logger?: HarnessLogger;
  /** Crash tests stop here; production passes nothing. */
  checkpoint?: (point: BackfillCheckpoint) => void | Promise<void>;
  sleep?: (ms: number) => Promise<void>;
  pollMs?: number;
  batchSize?: number;
}

export interface BackfillPlanReport {
  digest: string;
  orgFilter: string | null;
  orgs: BackfillOrgSummary[];
  totals: { sessions: number; batches: number; counts: BackfillCounts };
  /** Orgs already backfilled: never planned again. `remaining`: deletions found there since, kept. */
  alreadyDone: Array<{
    orgId: string;
    digest: string;
    completedAt: number;
    remaining: number;
  }>;
}

export type BackfillOrgState = 'done' | 'already_done' | 'pending';

export interface BackfillExecuteReport {
  digest: string;
  status: 'completed' | 'already_done' | 'in_progress';
  orgs: Array<{
    orgId: string;
    state: BackfillOrgState;
    erased: number;
    withdrawn: number;
    skipped: number;
  }>;
}

export interface BackfillStatusReport {
  plan: Pick<
    BackfillPlan,
    | 'digest'
    | 'state'
    | 'orgFilter'
    | 'operator'
    | 'items'
    | 'cursor'
    | 'staleReason'
  > | null;
  markers: BackfillMarker[];
}

interface Computed {
  digest: string;
  orgs: BackfillOrgSummary[];
  items: number;
  leftovers: Map<string, number>;
}

const SETTLED: ReadonlySet<BackfillItemState> = new Set([
  'erased',
  'withdrawn',
  'skipped_absent',
  'skipped_queued',
]);

const sleepFor = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The operator's backfill of conversations deleted before erasure existed
 * (E7B). `plan` is the dry run: it reads, counts and digests, and writes
 * nothing. `execute` needs that digest and erases only what it covers, one
 * bounded batch at a time, each re-checked against the data before it is
 * enqueued; the intent of every batch is recorded before it is acted on, so a
 * crash anywhere resumes from the plan's cursor.
 */
export class ErasureBackfillService<Tx> {
  private readonly logger: HarnessLogger;
  private readonly batchSize: number;

  constructor(private readonly deps: ErasureBackfillDependencies<Tx>) {
    this.logger = deps.logger ?? silentHarnessLogger;
    this.batchSize = deps.batchSize ?? BACKFILL_BATCH_SIZE;
  }

  async plan(orgFilter: string | null): Promise<BackfillPlanReport> {
    const markers = await this.markersByOrg();
    const computed = await this.compute(orgFilter, markers, null);
    const totals = computed.orgs.reduce(
      (sum, org) => ({
        sessions: sum.sessions + org.sessions,
        batches: sum.batches + org.batches,
        counts: addBackfillCounts(sum.counts, org.counts),
      }),
      { sessions: 0, batches: 0, counts: zeroBackfillCounts() },
    );
    this.logger.info(
      'erasure_backfill.planned',
      harnessLogMeta('erasure_backfill.planned', {
        digest: computed.digest,
        orgs: computed.orgs.length,
        sessions: totals.sessions,
        batches: totals.batches,
      }),
    );
    return {
      digest: computed.digest,
      orgFilter,
      orgs: computed.orgs,
      totals,
      alreadyDone: [...markers.values()]
        .filter((marker) => orgFilter === null || marker.orgId === orgFilter)
        .map((marker) => ({
          orgId: marker.orgId,
          digest: marker.digest,
          completedAt: marker.completedAt,
          remaining: computed.leftovers.get(marker.orgId) ?? 0,
        })),
    };
  }

  async execute(request: {
    digest: string;
    orgFilter: string | null;
    operator: string;
    waitMs?: number;
  }): Promise<BackfillExecuteReport> {
    const { lease, holderId } = this.deps;
    if (
      !(await lease.tryAcquire(
        BACKFILL_LEASE_NAME,
        holderId,
        BACKFILL_LEASE_MS,
      ))
    ) {
      throw this.refused(
        'backfill_in_progress',
        'Another backfill execution holds the lease',
        request.digest,
      );
    }
    try {
      const deadline =
        (await this.deps.store.now()) +
        (request.waitMs ?? BACKFILL_DEFAULT_WAIT_MS);
      const plan = await this.readyPlan(request);
      if (plan === null || plan.state === 'completed') {
        return await this.alreadyDone(request.digest, plan);
      }
      return await this.run(plan, request.operator, deadline);
    } finally {
      await lease.release(BACKFILL_LEASE_NAME, holderId).catch(() => undefined);
    }
  }

  async status(digest: string | null): Promise<BackfillStatusReport> {
    const plan = digest === null ? null : await this.deps.store.plan(digest);
    return {
      plan:
        plan === null
          ? null
          : {
              digest: plan.digest,
              state: plan.state,
              orgFilter: plan.orgFilter,
              operator: plan.operator,
              items: plan.items,
              cursor: plan.cursor,
              staleReason: plan.staleReason,
            },
      markers: await this.deps.store.markers(),
    };
  }

  /**
   * The stored plan with this digest, ready to run; on its first execution it
   * is computed again from the data and must equal the reviewed digest before
   * anything is written. Null: the digest covers nothing to erase.
   */
  private async readyPlan(request: {
    digest: string;
    orgFilter: string | null;
    operator: string;
  }): Promise<BackfillPlan | null> {
    const { store } = this.deps;
    let plan = await store.plan(request.digest);
    if (plan?.state === 'stale') {
      throw this.refused(
        'plan_stale',
        `The plan is stale (${plan.staleReason ?? 'data changed'}); run the dry run again`,
        request.digest,
      );
    }
    if (plan !== null && plan.state !== 'persisting') {
      if (plan.state === 'ready') await this.checkStoredItems(plan);
      return plan;
    }
    const orgFilter = plan?.orgFilter ?? request.orgFilter;
    const markers = await this.markersByOrg();
    const computed = await this.compute(orgFilter, markers, null);
    if (computed.digest !== request.digest) {
      throw this.refused(
        'plan_digest_mismatch',
        'The data no longer matches the reviewed plan; run the dry run again and review it',
        request.digest,
      );
    }
    if (computed.items === 0) return null;
    plan = await store.createPlan({
      digest: request.digest,
      orgFilter,
      operator: request.operator,
      createdAt: await store.now(),
      state: 'persisting',
      orgs: computed.orgs,
      items: computed.items,
      cursor: { ...INITIAL_BACKFILL_CURSOR },
      staleReason: null,
    });
    const persisted = await this.compute(orgFilter, markers, request.digest);
    if (persisted.digest !== request.digest) {
      await store.setPlanState(request.digest, 'persisting', 'stale', {
        staleReason: 'changed_while_persisting',
      });
      throw this.refused(
        'plan_digest_mismatch',
        'The data changed while the plan was being recorded; run the dry run again',
        request.digest,
      );
    }
    await store.setPlanState(request.digest, 'persisting', 'ready');
    await this.checkpoint('after_plan_persisted');
    return { ...plan, state: 'ready' };
  }

  /**
   * Reads every deleted conversation in session-id order and keeps the
   * backfill's candidates, numbered into per-org batches; with `persistAs`
   * it also stores them as that plan's items.
   */
  private async compute(
    orgFilter: string | null,
    markers: ReadonlyMap<string, BackfillMarker>,
    persistAs: string | null,
  ): Promise<Computed> {
    const digester = new BackfillDigester(orgFilter);
    const perOrg = new Map<
      string,
      { sessions: number; counts: BackfillCounts }
    >();
    const leftovers = new Map<string, number>();
    let items = 0;
    let after: string | null = null;
    for (;;) {
      const page = await this.deps.source.deletedAfter(
        after,
        BACKFILL_SCAN_PAGE,
        orgFilter,
      );
      if (page.length === 0) break;
      after = page[page.length - 1]?.sessionId ?? after;
      const jobs = await this.jobsOf(page);
      const planned: BackfillItem[] = [];
      for (const observation of page) {
        const job =
          jobs.get(observation.orgId)?.get(observation.sessionId) ?? null;
        if (!isBackfillCandidate(observation, job)) continue;
        if (markers.has(observation.orgId)) {
          leftovers.set(
            observation.orgId,
            (leftovers.get(observation.orgId) ?? 0) + 1,
          );
          continue;
        }
        const org = perOrg.get(observation.orgId) ?? {
          sessions: 0,
          counts: zeroBackfillCounts(),
        };
        const item: BackfillItem = {
          digest: persistAs ?? '',
          orgId: observation.orgId,
          sessionId: observation.sessionId,
          batch: Math.floor(org.sessions / this.batchSize),
          fingerprint: backfillFingerprint(observation),
          by: observation.by,
          updatedAt: observation.updatedAt,
          counts: { ...observation.counts },
          state: 'planned',
        };
        org.sessions += 1;
        org.counts = addBackfillCounts(org.counts, observation.counts);
        perOrg.set(observation.orgId, org);
        digester.add(item);
        planned.push(item);
        items += 1;
      }
      if (persistAs !== null && planned.length > 0) {
        await this.deps.store.putItems(planned);
      }
      if (page.length < BACKFILL_SCAN_PAGE) break;
    }
    return {
      digest: digester.digest(),
      orgs: [...perOrg.entries()]
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(([orgId, org]) => ({
          orgId,
          sessions: org.sessions,
          batches: Math.ceil(org.sessions / this.batchSize),
          counts: org.counts,
        })),
      items,
      leftovers,
    };
  }

  /** The stored items must still be exactly what was digested, batch numbers included. */
  private async checkStoredItems(plan: BackfillPlan): Promise<void> {
    const digester = new BackfillDigester(plan.orgFilter);
    const perOrg = new Map<string, number>();
    let count = 0;
    let after: string | null = null;
    let intact = true;
    for (;;) {
      const page = await this.deps.store.items(
        plan.digest,
        after,
        BACKFILL_SCAN_PAGE,
      );
      if (page.length === 0) break;
      for (const item of page) {
        const index = perOrg.get(item.orgId) ?? 0;
        perOrg.set(item.orgId, index + 1);
        if (
          item.fingerprint !== backfillFingerprint(item) ||
          item.batch !== Math.floor(index / this.batchSize)
        ) {
          intact = false;
        }
        digester.add(item);
        count += 1;
      }
      after = page[page.length - 1]?.sessionId ?? after;
      if (page.length < BACKFILL_SCAN_PAGE) break;
    }
    if (!intact || count !== plan.items || digester.digest() !== plan.digest) {
      throw this.refused(
        'plan_tampered',
        'The stored plan no longer matches its digest',
        plan.digest,
      );
    }
  }

  private async run(
    initial: BackfillPlan,
    operator: string,
    deadline: number,
  ): Promise<BackfillExecuteReport> {
    const { store } = this.deps;
    let cursor = initial.cursor;
    while (cursor.org < initial.orgs.length) {
      await this.renewLease(initial.digest);
      const org = initial.orgs[cursor.org] as BackfillOrgSummary;
      // Each org's batches run as that org, which the stored plan names.
      const inOrg = <T>(work: () => Promise<T>): Promise<T> =>
        forEnumeratedOrg(org.orgId, BACKFILL_JOB, work);
      const marker = await inOrg(() => store.marker(org.orgId));
      if (
        cursor.batch >= org.batches ||
        (marker !== null && marker.digest !== initial.digest)
      ) {
        if (marker === null)
          await inOrg(() => this.completeOrg(initial, org, operator));
        cursor = await this.move(initial.digest, cursor, {
          org: cursor.org + 1,
          batch: 0,
          phase: 'next',
        });
        continue;
      }
      if (cursor.phase === 'next') {
        await this.checkpoint('before_intent');
        cursor = await this.move(initial.digest, cursor, {
          ...cursor,
          phase: 'intended',
        });
        await this.checkpoint('after_intent');
      } else if (cursor.phase === 'intended') {
        const at = cursor;
        cursor = await inOrg(() =>
          this.enqueueBatch(initial, org.orgId, at, operator),
        );
        await this.checkpoint('after_enqueue');
      } else {
        if (
          !(await inOrg(() =>
            this.settleBatch(initial.digest, org.orgId, cursor.batch, deadline),
          ))
        ) {
          return this.report(initial, 'in_progress');
        }
        await this.checkpoint('after_settle');
        cursor = await this.move(initial.digest, cursor, {
          org: cursor.org,
          batch: cursor.batch + 1,
          phase: 'next',
        });
      }
    }
    await store.setPlanState(initial.digest, 'ready', 'completed');
    this.logger.info(
      'erasure_backfill.completed',
      harnessLogMeta('erasure_backfill.completed', {
        digest: initial.digest,
        orgs: initial.orgs.length,
        operator,
      }),
    );
    return this.report(initial, 'completed');
  }

  /**
   * Re-checks the batch against the data with the plan's own predicate and
   * fingerprints, then, in one transaction, enqueues its erasures, records
   * the items' states and appends the batch's audit record. Any planned
   * conversation that changed makes the whole plan stale before anything of
   * this batch is enqueued.
   */
  private async enqueueBatch(
    plan: BackfillPlan,
    orgId: string,
    cursor: BackfillCursor,
    operator: string,
  ): Promise<BackfillCursor> {
    const items = await this.deps.store.batchItems(
      plan.digest,
      orgId,
      cursor.batch,
    );
    const planned = items.filter((item) => item.state === 'planned');
    const ids = planned.map((item) => item.sessionId);
    const observed = new Map(
      (await this.deps.source.observe(orgId, ids)).map((observation) => [
        observation.sessionId,
        observation,
      ]),
    );
    const jobs = await this.deps.jobs.findMany(orgId, ids);
    const enqueue: BackfillItem[] = [];
    const skipped: Array<{ sessionId: string; state: BackfillItemState }> = [];
    for (const item of planned) {
      const observation = observed.get(item.sessionId);
      if (observation === undefined) {
        skipped.push({ sessionId: item.sessionId, state: 'skipped_absent' });
      } else if (jobs.has(item.sessionId)) {
        skipped.push({ sessionId: item.sessionId, state: 'skipped_queued' });
      } else if (
        !isBackfillCandidate(observation, null) ||
        backfillFingerprint(observation) !== item.fingerprint
      ) {
        await this.deps.store.setPlanState(plan.digest, 'ready', 'stale', {
          staleReason: observation.deleted
            ? 'conversation_changed'
            : 'conversation_live',
        });
        this.logger.warn(
          'erasure_backfill.stale',
          harnessLogMeta('erasure_backfill.stale', {
            digest: plan.digest,
            orgId,
            batch: cursor.batch,
            reason: observation.deleted
              ? 'conversation_changed'
              : 'conversation_live',
          }),
        );
        throw this.refused(
          'plan_stale',
          'A planned conversation changed since the dry run; run the dry run again',
          plan.digest,
        );
      } else {
        enqueue.push(item);
      }
    }
    const enqueued: BackfillCursor = { ...cursor, phase: 'enqueued' };
    const ts = new Date(await this.deps.store.now()).toISOString();
    await this.deps.transaction(async (tx) => {
      if (
        !(await this.deps.store.moveCursor(plan.digest, cursor, enqueued, tx))
      ) {
        throw this.lost(plan.digest);
      }
      await this.deps.jobs.enqueueMany(
        enqueue.map((item) => ({
          orgId,
          sessionId: item.sessionId,
          reason: 'user_delete' as const,
          by: item.by,
        })),
        tx,
      );
      await this.deps.store.setItemStates(
        plan.digest,
        [
          ...enqueue.map((item) => ({
            sessionId: item.sessionId,
            state: 'enqueued' as const,
          })),
          ...skipped,
        ],
        tx,
      );
      await this.checkpoint('in_enqueue_transaction');
      await this.deps.audit.append(
        orgId,
        [
          backfillBatchAuditEntry(
            { digest: plan.digest, operator },
            orgId,
            cursor.batch,
            enqueue,
            skipped.length,
          ),
        ],
        ts,
        tx,
      );
    }, orgId);
    this.logger.info(
      'erasure_backfill.batch_enqueued',
      harnessLogMeta('erasure_backfill.batch_enqueued', {
        digest: plan.digest,
        orgId,
        batch: cursor.batch,
        sessions: enqueue.length,
        skipped: skipped.length,
      }),
    );
    return enqueued;
  }

  /** True once every erasure of the batch finished or was withdrawn; false when the wait ran out. */
  private async settleBatch(
    digest: string,
    orgId: string,
    batch: number,
    deadline: number,
  ): Promise<boolean> {
    const items = await this.deps.store.batchItems(digest, orgId, batch);
    const open = items
      .filter((item) => item.state === 'enqueued')
      .map((item) => item.sessionId);
    let jobs = await this.deps.jobs.findMany(orgId, open);
    while ([...jobs.values()].some((job) => job.status === 'pending')) {
      if ((await this.deps.store.now()) >= deadline) return false;
      await (this.deps.sleep ?? sleepFor)(this.deps.pollMs ?? BACKFILL_POLL_MS);
      await this.renewLease(digest);
      jobs = await this.deps.jobs.findMany(orgId, open);
    }
    const states = open.map(
      (sessionId): { sessionId: string; state: BackfillItemState } => ({
        sessionId,
        state: jobs.has(sessionId) ? 'erased' : 'withdrawn',
      }),
    );
    await this.deps.store.setItemStates(digest, states);
    this.logger.info(
      'erasure_backfill.batch_settled',
      harnessLogMeta('erasure_backfill.batch_settled', {
        digest,
        orgId,
        batch,
        erased: states.filter((entry) => entry.state === 'erased').length,
        withdrawn: states.filter((entry) => entry.state === 'withdrawn').length,
      }),
    );
    return true;
  }

  /** The org's marker and its audit record, once: a second completion writes neither. */
  private async completeOrg(
    plan: BackfillPlan,
    org: BackfillOrgSummary,
    operator: string,
  ): Promise<void> {
    const tally = await this.tally(plan.digest, org);
    const marker: BackfillMarker = {
      orgId: org.orgId,
      digest: plan.digest,
      operator,
      completedAt: await this.deps.store.now(),
      ...tally,
    };
    const ts = new Date(marker.completedAt).toISOString();
    await this.deps.transaction(async (tx) => {
      if (await this.deps.store.putMarker(marker, tx)) {
        await this.deps.audit.append(
          org.orgId,
          [backfillCompletedAuditEntry(marker)],
          ts,
          tx,
        );
      }
    }, org.orgId);
    await this.checkpoint('after_marker');
    this.logger.info(
      'erasure_backfill.org_completed',
      harnessLogMeta('erasure_backfill.org_completed', {
        digest: plan.digest,
        orgId: org.orgId,
        ...tally,
      }),
    );
  }

  private async tally(
    digest: string,
    org: BackfillOrgSummary,
  ): Promise<{ erased: number; withdrawn: number; skipped: number }> {
    const tally = { erased: 0, withdrawn: 0, skipped: 0 };
    for (let batch = 0; batch < org.batches; batch += 1) {
      for (const item of await this.deps.store.batchItems(
        digest,
        org.orgId,
        batch,
      )) {
        if (item.state === 'erased') tally.erased += 1;
        else if (item.state === 'withdrawn') tally.withdrawn += 1;
        else if (SETTLED.has(item.state)) tally.skipped += 1;
      }
    }
    return tally;
  }

  private async report(
    plan: BackfillPlan,
    status: BackfillExecuteReport['status'],
  ): Promise<BackfillExecuteReport> {
    const orgs: BackfillExecuteReport['orgs'] = [];
    for (const org of plan.orgs) {
      const marker = await this.deps.store.marker(org.orgId);
      orgs.push(
        marker === null
          ? {
              orgId: org.orgId,
              state: 'pending',
              ...(await this.tally(plan.digest, org)),
            }
          : {
              orgId: org.orgId,
              state: marker.digest === plan.digest ? 'done' : 'already_done',
              erased: marker.erased,
              withdrawn: marker.withdrawn,
              skipped: marker.skipped,
            },
      );
    }
    return { digest: plan.digest, status, orgs };
  }

  private async alreadyDone(
    digest: string,
    plan: BackfillPlan | null,
  ): Promise<BackfillExecuteReport> {
    this.logger.info(
      'erasure_backfill.already_done',
      harnessLogMeta('erasure_backfill.already_done', { digest }),
    );
    if (plan !== null) return this.report(plan, 'already_done');
    return { digest, status: 'already_done', orgs: [] };
  }

  private async move(
    digest: string,
    from: BackfillCursor,
    to: BackfillCursor,
  ): Promise<BackfillCursor> {
    if (!(await this.deps.store.moveCursor(digest, from, to))) {
      throw this.lost(digest);
    }
    return to;
  }

  private async renewLease(digest: string): Promise<void> {
    if (
      !(await this.deps.lease.tryAcquire(
        BACKFILL_LEASE_NAME,
        this.deps.holderId,
        BACKFILL_LEASE_MS,
      ))
    ) {
      throw this.lost(digest);
    }
  }

  private lost(digest: string): BackfillRefused {
    return this.refused(
      'backfill_in_progress',
      'Another execution moved this plan; this one stops',
      digest,
    );
  }

  private async jobsOf(
    page: readonly { orgId: string; sessionId: string }[],
  ): Promise<Map<string, Map<string, ErasureJob>>> {
    const byOrg = new Map<string, string[]>();
    for (const { orgId, sessionId } of page) {
      byOrg.set(orgId, [...(byOrg.get(orgId) ?? []), sessionId]);
    }
    const jobs = new Map<string, Map<string, ErasureJob>>();
    for (const [orgId, ids] of byOrg) {
      jobs.set(orgId, await this.deps.jobs.findMany(orgId, ids));
    }
    return jobs;
  }

  private async markersByOrg(): Promise<Map<string, BackfillMarker>> {
    return new Map(
      (await this.deps.store.markers()).map((marker) => [marker.orgId, marker]),
    );
  }

  private async checkpoint(point: BackfillCheckpoint): Promise<void> {
    await this.deps.checkpoint?.(point);
  }

  private refused(
    reasonCode: BackfillRefused['reasonCode'],
    message: string,
    digest: string,
  ): BackfillRefused {
    this.logger.warn(
      'erasure_backfill.refused',
      harnessLogMeta('erasure_backfill.refused', { reasonCode, digest }),
    );
    return new BackfillRefused(reasonCode, message);
  }
}
