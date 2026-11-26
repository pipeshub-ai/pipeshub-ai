import type { Tx } from '../../uow';
import {
  ORG_ERASURE_STEPS,
  PrincipalErasureJob,
  PrincipalErasureStep,
  USER_ERASURE_STEPS,
  orgErasureJobId,
  userErasureJobId,
} from '../../../../modules/agent-harness/domain/principal-erasure';
import type { OrgDataKeys } from '../../../../modules/agent-harness/application/org-data-keys';
import {
  HarnessRun,
  TERMINAL_RUN_STATUSES,
} from '../../../../modules/agent-harness/domain/execution';
import type {
  OrgDataStore,
  PrincipalErasureJobStore,
} from '../../../../modules/agent-harness/ports/principal-erasure';
import { getConversationRepository } from '../../conversation';
import { getNotificationRepository } from '../../notification';
import { getOutboxRepository } from '../../outbox';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';
import { PgPolicyStore } from '../../../../modules/control-plane';
import { runFromDocument } from '../execution/document';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  orgUnit,
  rowRegion,
} from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';
import type { PrincipalErasureDocument } from './document';

type StoredJob = Omit<PrincipalErasureDocument, '_id'>;

const REASON = 'harness principal erasure';

const toJob = (doc: StoredJob): PrincipalErasureJob => ({
  kind: doc.kind,
  orgId: doc.orgId,
  userId: doc.userId ?? null,
  by: doc.by,
  step: doc.step,
  requestedAt: doc.requestedAt,
  auditRetentionDays: doc.auditRetentionDays,
  auditRemoveAfter: doc.auditRemoveAfter ?? null,
  cursor: doc.cursor ?? null,
  transfers: [...doc.transfers],
  journaled: doc.journaled,
  counts: { ...doc.counts },
  extension: doc.extension ?? null,
  failures: doc.failures,
  nextAttemptAt: doc.nextAttemptAt,
  lastErrorType: doc.lastErrorType ?? null,
});

const jobId = (
  job: Pick<PrincipalErasureJob, 'kind' | 'orgId' | 'userId'>,
): string =>
  job.kind === 'org'
    ? orgErasureJobId(job.orgId)
    : userErasureJobId(job.orgId, job.userId ?? '');

/**
 * Org and user deletions on PostgreSQL (`harness.principal_erasures`), one row per deleted org or user. The request
 * joins the deletion's own unit (as that org); the retention sweeper reads and moves the jobs as the jobs role, each
 * write guarded by the step it read.
 */
export class PgPrincipalErasureJobs implements PrincipalErasureJobStore<Tx> {
  constructor(private readonly time: PgTime = pgServerTime) {}

  now(): Promise<number> {
    return jobsUnit(REASON, { write: false }, (k) => this.time.now(k));
  }

  requestOrg(
    request: {
      orgId: string;
      by: string;
      requestedAt: number;
      auditRetentionDays: number;
      extension?: Readonly<Record<string, unknown>> | null;
    },
    tx: Tx,
  ): Promise<boolean> {
    const doc: StoredJob = {
      kind: 'org',
      orgId: request.orgId,
      userId: null,
      by: request.by,
      step: ORG_ERASURE_STEPS[0],
      requestedAt: request.requestedAt,
      auditRetentionDays: request.auditRetentionDays,
      auditRemoveAfter: null,
      cursor: null,
      transfers: [],
      journaled: 0,
      counts: {},
      extension: request.extension ?? null,
      failures: 0,
      nextAttemptAt: request.requestedAt,
      lastErrorType: null,
    };
    return orgOrJobsUnit(
      tx,
      request.orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rows } = await k.query(
          `/* pipeshub:harness.principal_erasures_request_org */ INSERT INTO harness.principal_erasures
             (org_id, id, doc, region) VALUES ($1, $2, $3::jsonb, $4)
           ON CONFLICT (org_id, id) DO NOTHING RETURNING 1 AS created`,
          [
            request.orgId,
            orgErasureJobId(request.orgId),
            docParam(doc),
            rowRegion(),
          ],
        );
        return rows.length === 1;
      },
    );
  }

  async requestUser(
    request: {
      orgId: string;
      userId: string;
      by: string;
      requestedAt: number;
      transfers: PrincipalErasureJob['transfers'];
    },
    tx: Tx,
  ): Promise<void> {
    const doc: StoredJob = {
      kind: 'user',
      orgId: request.orgId,
      userId: request.userId,
      by: request.by,
      step: USER_ERASURE_STEPS[0],
      requestedAt: request.requestedAt,
      auditRetentionDays: 0,
      auditRemoveAfter: null,
      cursor: null,
      transfers: request.transfers,
      journaled: 0,
      counts: {},
      failures: 0,
      nextAttemptAt: request.requestedAt,
      lastErrorType: null,
    };
    // A user deleted again (re-invited, then deleted) adds to the waiting job rather than replacing it.
    await orgOrJobsUnit(tx, request.orgId, REASON, { write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.principal_erasures_request_user */ INSERT INTO harness.principal_erasures AS j
           (org_id, id, doc, region) VALUES ($1, $2, $3::jsonb, $4)
         ON CONFLICT (org_id, id) DO UPDATE
           SET doc = jsonb_set(j.doc, '{transfers}', (j.doc->'transfers') || (EXCLUDED.doc->'transfers'))`,
        [
          request.orgId,
          userErasureJobId(request.orgId, request.userId),
          docParam(doc),
          rowRegion(),
        ],
      ),
    );
  }

  findOrg(orgId: string): Promise<PrincipalErasureJob | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.principal_erasures_find_org */ SELECT doc FROM harness.principal_erasures
            WHERE org_id = $1 AND id = $2`,
          [orgId, orgErasureJobId(orgId)],
        );
        return rows[0] === undefined
          ? null
          : toJob(decodeDoc<StoredJob>(rows[0].doc));
      },
    );
  }

  due(now: number, limit: number): Promise<PrincipalErasureJob[]> {
    return jobsUnit(REASON, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.principal_erasures_due */ SELECT doc FROM harness.principal_erasures
          WHERE next_attempt_at_ms <= $1 ORDER BY next_attempt_at_ms, org_id, id LIMIT $2`,
        [now, limit],
      );
      return rows.map((row) => toJob(decodeDoc<StoredJob>(row.doc)));
    });
  }

  writeProgress(
    job: PrincipalErasureJob,
    expectedStep: PrincipalErasureStep,
    tx?: Tx,
  ): Promise<boolean> {
    const progress = {
      step: job.step,
      cursor: job.cursor,
      journaled: job.journaled,
      counts: job.counts,
      failures: job.failures,
      nextAttemptAt: job.nextAttemptAt,
      lastErrorType: job.lastErrorType,
      auditRemoveAfter: job.auditRemoveAfter,
      extension: job.extension,
    };
    return orgOrJobsUnit(tx, job.orgId, REASON, { write: true }, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.principal_erasures_progress */ UPDATE harness.principal_erasures
            SET doc = doc || $4::jsonb
          WHERE org_id = $1 AND id = $2 AND step = $3`,
        [job.orgId, jobId(job), expectedStep, docParam(progress)],
      );
      return rowCount === 1;
    });
  }

  async remove(job: PrincipalErasureJob): Promise<void> {
    await orgOrJobsUnit(undefined, job.orgId, REASON, { write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.principal_erasures_remove */ DELETE FROM harness.principal_erasures
          WHERE org_id = $1 AND id = $2`,
        [job.orgId, jobId(job)],
      ),
    );
  }
}

/**
 * Where each PostgreSQL harness table that holds org rows stands in an org's erasure (the counterpart of
 * `ORG_STORE_COVERAGE`): `org_stores` tables are emptied of the org by `eraseStores`, `audit` ones by `eraseAudit` after
 * the audit retention, `data_keys` last; `principals` tables are erased by the user and org hooks; `none` holds the
 * job itself. A test keeps this equal to the catalog, so a new org table must say where it stands.
 */
export const PG_ORG_TABLE_COVERAGE: Readonly<
  Record<string, 'org_stores' | 'audit' | 'data_keys' | 'principals' | 'none'>
> = {
  session_events: 'org_stores',
  stream_counters: 'org_stores',
  journal_payloads: 'org_stores',
  runs: 'org_stores',
  run_events: 'org_stores',
  session_counters: 'org_stores',
  command_receipts: 'org_stores',
  admission_receipts: 'org_stores',
  admission_quotas: 'org_stores',
  next_runs: 'org_stores',
  budget_accounts: 'org_stores',
  budget_reservations: 'org_stores',
  audit_chain_checks: 'org_stores',
  sandbox_snapshots: 'org_stores',
  personal_choices: 'org_stores',
  workspace_heads: 'org_stores',
  workspace_manifests: 'org_stores',
  workspace_keys: 'org_stores',
  workspace_writers: 'org_stores',
  schedule_firings: 'org_stores',
  schedule_counters: 'org_stores',
  cp_org_heads: 'org_stores',
  cp_policy_layers: 'org_stores',
  cp_policy_history: 'org_stores',
  cp_migration_ledger: 'org_stores',
  cp_catalog_items: 'org_stores',
  cp_installations: 'org_stores',
  cp_installation_history: 'org_stores',
  cp_grants: 'org_stores',
  cp_capability_ledger: 'org_stores',
  cp_bindings: 'org_stores',
  session_forks: 'org_stores',
  session_erasures: 'org_stores',
  agent_erasures: 'org_stores',
  erasure_backfill_items: 'org_stores',
  erasure_backfill_markers: 'org_stores',
  audit_records: 'audit',
  audit_heads: 'audit',
  audit_pending: 'audit',
  audit_anchors: 'audit',
  data_keys: 'data_keys',
  memories: 'principals',
  schedules: 'principals',
  engine_signins: 'principals',
  // ADR-INBOX-01: the inbox store's own user and org hooks erase them.
  inbox_preferences: 'principals',
  run_end_notices: 'principals',
  run_watchers: 'principals',
  principal_erasures: 'none',
};

/** One batch of an org's rows of a table, as literal statements (a table name is never built into SQL text). */
const ORG_BATCH_DELETES: Readonly<Record<string, string>> = {
  session_events: `/* pipeshub:harness.org_erase_session_events */ DELETE FROM harness.session_events
    WHERE ctid IN (SELECT ctid FROM harness.session_events WHERE org_id = $1 LIMIT $2)`,
  stream_counters: `/* pipeshub:harness.org_erase_stream_counters */ DELETE FROM harness.stream_counters
    WHERE ctid IN (SELECT ctid FROM harness.stream_counters WHERE org_id = $1 LIMIT $2)`,
  journal_payloads: `/* pipeshub:harness.org_erase_journal_payloads */ DELETE FROM harness.journal_payloads
    WHERE ctid IN (SELECT ctid FROM harness.journal_payloads WHERE org_id = $1 LIMIT $2)`,
  runs: `/* pipeshub:harness.org_erase_runs */ DELETE FROM harness.runs
    WHERE ctid IN (SELECT ctid FROM harness.runs WHERE org_id = $1 LIMIT $2)`,
  run_events: `/* pipeshub:harness.org_erase_run_events */ DELETE FROM harness.run_events
    WHERE ctid IN (SELECT ctid FROM harness.run_events WHERE org_id = $1 LIMIT $2)`,
  session_counters: `/* pipeshub:harness.org_erase_session_counters */ DELETE FROM harness.session_counters
    WHERE ctid IN (SELECT ctid FROM harness.session_counters WHERE org_id = $1 LIMIT $2)`,
  command_receipts: `/* pipeshub:harness.org_erase_command_receipts */ DELETE FROM harness.command_receipts
    WHERE ctid IN (SELECT ctid FROM harness.command_receipts WHERE org_id = $1 LIMIT $2)`,
  admission_receipts: `/* pipeshub:harness.org_erase_admission_receipts */ DELETE FROM harness.admission_receipts
    WHERE ctid IN (SELECT ctid FROM harness.admission_receipts WHERE org_id = $1 LIMIT $2)`,
  admission_quotas: `/* pipeshub:harness.org_erase_admission_quotas */ DELETE FROM harness.admission_quotas
    WHERE ctid IN (SELECT ctid FROM harness.admission_quotas WHERE org_id = $1 LIMIT $2)`,
  next_runs: `/* pipeshub:harness.org_erase_next_runs */ DELETE FROM harness.next_runs
    WHERE ctid IN (SELECT ctid FROM harness.next_runs WHERE org_id = $1 LIMIT $2)`,
  budget_accounts: `/* pipeshub:harness.org_erase_budget_accounts */ DELETE FROM harness.budget_accounts
    WHERE ctid IN (SELECT ctid FROM harness.budget_accounts WHERE org_id = $1 LIMIT $2)`,
  budget_reservations: `/* pipeshub:harness.org_erase_budget_reservations */ DELETE FROM harness.budget_reservations
    WHERE ctid IN (SELECT ctid FROM harness.budget_reservations WHERE org_id = $1 LIMIT $2)`,
  audit_chain_checks: `/* pipeshub:harness.org_erase_audit_chain_checks */ DELETE FROM harness.audit_chain_checks
    WHERE ctid IN (SELECT ctid FROM harness.audit_chain_checks WHERE org_id = $1 LIMIT $2)`,
  sandbox_snapshots: `/* pipeshub:harness.org_erase_sandbox_snapshots */ DELETE FROM harness.sandbox_snapshots
    WHERE ctid IN (SELECT ctid FROM harness.sandbox_snapshots WHERE org_id = $1 LIMIT $2)`,
  personal_choices: `/* pipeshub:harness.org_erase_personal_choices */ DELETE FROM harness.personal_choices
    WHERE ctid IN (SELECT ctid FROM harness.personal_choices WHERE org_id = $1 LIMIT $2)`,
  workspace_heads: `/* pipeshub:harness.org_erase_workspace_heads */ DELETE FROM harness.workspace_heads
    WHERE ctid IN (SELECT ctid FROM harness.workspace_heads WHERE org_id = $1 LIMIT $2)`,
  workspace_manifests: `/* pipeshub:harness.org_erase_workspace_manifests */ DELETE FROM harness.workspace_manifests
    WHERE ctid IN (SELECT ctid FROM harness.workspace_manifests WHERE org_id = $1 LIMIT $2)`,
  workspace_keys: `/* pipeshub:harness.org_erase_workspace_keys */ DELETE FROM harness.workspace_keys
    WHERE ctid IN (SELECT ctid FROM harness.workspace_keys WHERE org_id = $1 LIMIT $2)`,
  workspace_writers: `/* pipeshub:harness.org_erase_workspace_writers */ DELETE FROM harness.workspace_writers
    WHERE ctid IN (SELECT ctid FROM harness.workspace_writers WHERE org_id = $1 LIMIT $2)`,
  schedule_firings: `/* pipeshub:harness.org_erase_schedule_firings */ DELETE FROM harness.schedule_firings
    WHERE ctid IN (SELECT ctid FROM harness.schedule_firings WHERE org_id = $1 LIMIT $2)`,
  schedule_counters: `/* pipeshub:harness.org_erase_schedule_counters */ DELETE FROM harness.schedule_counters
    WHERE ctid IN (SELECT ctid FROM harness.schedule_counters WHERE org_id = $1 LIMIT $2)`,
  cp_org_heads: `/* pipeshub:harness.org_erase_cp_org_heads */ DELETE FROM harness.cp_org_heads
    WHERE ctid IN (SELECT ctid FROM harness.cp_org_heads WHERE org_id = $1 LIMIT $2)`,
  cp_policy_history: `/* pipeshub:harness.org_erase_cp_policy_history */ DELETE FROM harness.cp_policy_history
    WHERE ctid IN (SELECT ctid FROM harness.cp_policy_history WHERE org_id = $1 LIMIT $2)`,
  cp_migration_ledger: `/* pipeshub:harness.org_erase_cp_migration_ledger */ DELETE FROM harness.cp_migration_ledger
    WHERE ctid IN (SELECT ctid FROM harness.cp_migration_ledger WHERE org_id = $1 LIMIT $2)`,
  cp_catalog_items: `/* pipeshub:harness.org_erase_cp_catalog_items */ DELETE FROM harness.cp_catalog_items
    WHERE ctid IN (SELECT ctid FROM harness.cp_catalog_items WHERE org_id = $1 LIMIT $2)`,
  cp_installations: `/* pipeshub:harness.org_erase_cp_installations */ DELETE FROM harness.cp_installations
    WHERE ctid IN (SELECT ctid FROM harness.cp_installations WHERE org_id = $1 LIMIT $2)`,
  cp_installation_history: `/* pipeshub:harness.org_erase_cp_installation_history */ DELETE FROM harness.cp_installation_history
    WHERE ctid IN (SELECT ctid FROM harness.cp_installation_history WHERE org_id = $1 LIMIT $2)`,
  cp_grants: `/* pipeshub:harness.org_erase_cp_grants */ DELETE FROM harness.cp_grants
    WHERE ctid IN (SELECT ctid FROM harness.cp_grants WHERE org_id = $1 LIMIT $2)`,
  cp_capability_ledger: `/* pipeshub:harness.org_erase_cp_capability_ledger */ DELETE FROM harness.cp_capability_ledger
    WHERE ctid IN (SELECT ctid FROM harness.cp_capability_ledger WHERE org_id = $1 LIMIT $2)`,
  cp_bindings: `/* pipeshub:harness.org_erase_cp_bindings */ DELETE FROM harness.cp_bindings
    WHERE ctid IN (SELECT ctid FROM harness.cp_bindings WHERE org_id = $1 LIMIT $2)`,
  session_forks: `/* pipeshub:harness.org_erase_session_forks */ DELETE FROM harness.session_forks
    WHERE ctid IN (SELECT ctid FROM harness.session_forks WHERE org_id = $1 LIMIT $2)`,
  session_erasures: `/* pipeshub:harness.org_erase_session_erasures */ DELETE FROM harness.session_erasures
    WHERE ctid IN (SELECT ctid FROM harness.session_erasures WHERE org_id = $1 LIMIT $2)`,
  agent_erasures: `/* pipeshub:harness.org_erase_agent_erasures */ DELETE FROM harness.agent_erasures
    WHERE ctid IN (SELECT ctid FROM harness.agent_erasures WHERE org_id = $1 LIMIT $2)`,
  erasure_backfill_items: `/* pipeshub:harness.org_erase_erasure_backfill_items */ DELETE FROM harness.erasure_backfill_items
    WHERE ctid IN (SELECT ctid FROM harness.erasure_backfill_items WHERE org_id = $1 LIMIT $2)`,
  erasure_backfill_markers: `/* pipeshub:harness.org_erase_erasure_backfill_markers */ DELETE FROM harness.erasure_backfill_markers
    WHERE ctid IN (SELECT ctid FROM harness.erasure_backfill_markers WHERE org_id = $1 LIMIT $2)`,
};

const AUDIT_BATCH_DELETES: Readonly<Record<string, string>> = {
  audit_records: `/* pipeshub:harness.org_erase_audit_records */ DELETE FROM harness.audit_records
    WHERE ctid IN (SELECT ctid FROM harness.audit_records WHERE org_id = $1 LIMIT $2)`,
  audit_heads: `/* pipeshub:harness.org_erase_audit_heads */ DELETE FROM harness.audit_heads
    WHERE ctid IN (SELECT ctid FROM harness.audit_heads WHERE org_id = $1 LIMIT $2)`,
  audit_pending: `/* pipeshub:harness.org_erase_audit_pending */ DELETE FROM harness.audit_pending
    WHERE ctid IN (SELECT ctid FROM harness.audit_pending WHERE org_id = $1 LIMIT $2)`,
  audit_anchors: `/* pipeshub:harness.org_erase_audit_anchors */ DELETE FROM harness.audit_anchors
    WHERE ctid IN (SELECT ctid FROM harness.audit_anchors WHERE org_id = $1 LIMIT $2)`,
};

const ORG_BATCH = 500;
const TERMINAL = [...TERMINAL_RUN_STATUSES];

/**
 * The org's data across the harness's tables and main's conversation and notification data on PostgreSQL
 * (CLEANUP-G1). The sweeper calls it with no org bound: each statement runs as the jobs role and names the org, or as
 * the org through main's repositories; deletes go in batches, so a large org never holds one long statement.
 */
export class PgOrgData implements OrgDataStore<Tx> {
  /** `keys`: the key ring whose cache the shred must drop too; absent, only the stored keys go. */
  constructor(private readonly keys?: Pick<OrgDataKeys, 'shred'>) {}

  async sessionsAfter(
    orgId: string,
    after: string | null,
    limit: number,
  ): Promise<string[]> {
    const ids = new Set(
      await inBoundOrgUnit(orgId, () =>
        getConversationRepository().listSessionIdsAfter(orgId, after, limit),
      ),
    );
    // Sessions with no conversation row: runs, journals and fork copies name them.
    const named = await orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ session_id: string }>(
          `/* pipeshub:harness.org_sessions_after */ SELECT session_id FROM (
             SELECT session_id FROM harness.session_counters WHERE org_id = $1 AND ($2::text IS NULL OR session_id > $2)
             UNION SELECT session_id FROM harness.runs WHERE org_id = $1 AND session_id IS NOT NULL
               AND ($2::text IS NULL OR session_id > $2)
             UNION SELECT session_id FROM harness.workspace_keys WHERE org_id = $1 AND ($2::text IS NULL OR session_id > $2)
           ) s ORDER BY session_id LIMIT $3`,
          [orgId, after, limit],
        );
        return rows.map((row) => row.session_id);
      },
    );
    for (const id of named) ids.add(id);
    return [...ids]
      .filter((id) => after === null || id > after)
      .sort()
      .slice(0, limit);
  }

  async markSessionsDeleted(
    orgId: string,
    sessionIds: readonly string[],
    tx: Tx,
  ): Promise<void> {
    if (sessionIds.length === 0) return;
    await orgUnit(tx, orgId, { write: true }, () =>
      getConversationRepository().markDeletedForErasure(
        orgId,
        sessionIds,
        Date.now(),
        tx,
      ),
    );
  }

  pendingSessionErasures(orgId: string): Promise<number> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ n: number }>(
          `/* pipeshub:harness.org_pending_session_erasures */ SELECT count(*)::int AS n FROM harness.session_erasures
            WHERE org_id = $1 AND status = 'pending'`,
          [orgId],
        );
        return rows[0]?.n ?? 0;
      },
    );
  }

  activeRuns(orgId: string): Promise<HarnessRun[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.org_active_runs */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND NOT (status = ANY($2::text[])) ORDER BY run_id`,
          [orgId, TERMINAL],
        );
        return rows.map((row) => runFromDocument(decodeDoc<object>(row.doc)));
      },
    );
  }

  async eraseStores(orgId: string): Promise<Record<string, number>> {
    const counts: Record<string, number> = {};
    const add = (name: string, removed: number): void => {
      if (removed > 0) counts[name] = (counts[name] ?? 0) + removed;
    };
    for (const [table, text] of Object.entries(ORG_BATCH_DELETES)) {
      add(table, await this.drain(orgId, text));
    }
    // Policy layers are written only by the policy store (MG-15), their erasure too.
    const policies = new PgPolicyStore();
    add(
      'cp_policy_layers',
      await this.drainBy(orgId, () =>
        policies.eraseOrgLayers(orgId, ORG_BATCH),
      ),
    );
    add('erasure_backfill_plans', await this.erasePlans(orgId));
    const conversations = getConversationRepository();
    await inBoundOrgUnit(orgId, async () => {
      for (;;) {
        const ids = await conversations.listSessionIdsAfter(
          orgId,
          null,
          ORG_BATCH,
        );
        if (ids.length === 0) break;
        for (const id of ids) {
          const removed = await conversations.discardSession(id, orgId);
          add('chat_sessions', removed.sessions);
          add('chat_messages', removed.messages);
        }
      }
      for (;;) {
        const removed = await conversations.deleteOrgCitations(
          orgId,
          ORG_BATCH,
        );
        if (removed === 0) break;
        add('citations', removed);
      }
      for (;;) {
        const removed = await getNotificationRepository().eraseForOrg(
          orgId,
          ORG_BATCH,
        );
        if (removed === 0) break;
        add('notifications', removed);
      }
      for (;;) {
        const removed = await getOutboxRepository().eraseDeliveredForOrg(
          orgId,
          ORG_BATCH,
        );
        if (removed === 0) break;
        add('outbox', removed);
      }
    });
    return counts;
  }

  async destroyDataKeys(orgId: string): Promise<number> {
    if (this.keys !== undefined) return this.keys.shred(orgId);
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.org_erase_data_keys */ DELETE FROM harness.data_keys WHERE org_id = $1`,
          [orgId],
        );
        return rowCount ?? 0;
      },
    );
  }

  async eraseAudit(orgId: string): Promise<number> {
    let removed = 0;
    for (const text of Object.values(AUDIT_BATCH_DELETES)) {
      removed += await this.drain(orgId, text);
    }
    return removed;
  }

  /** Runs one table's batch delete until it removes nothing; the rows removed. */
  private drain(orgId: string, text: string): Promise<number> {
    return this.drainBy(orgId, () =>
      orgUnit(
        undefined,
        orgId,
        { write: true },
        async (k) => (await k.query(text, [orgId, ORG_BATCH])).rowCount ?? 0,
      ),
    );
  }

  /** Runs one batch as the org until it removes nothing; the rows removed. */
  private async drainBy(
    orgId: string,
    batch: () => Promise<number>,
  ): Promise<number> {
    let removed = 0;
    for (;;) {
      // As the org itself: some org tables (the control plane's) are not open to the jobs role.
      const n = await inBoundOrgUnit(orgId, batch);
      if (n === 0) return removed;
      removed += n;
    }
  }

  /** The org's own plans go; a plan over several orgs keeps its other orgs' summaries. */
  private erasePlans(orgId: string): Promise<number> {
    return jobsUnit(REASON, { write: true }, async (k) => {
      const own = await k.query(
        `/* pipeshub:harness.org_erase_backfill_plans */ DELETE FROM harness.erasure_backfill_plans WHERE org_filter = $1`,
        [orgId],
      );
      const shared = await k.query(
        `/* pipeshub:harness.org_strip_backfill_plans */ UPDATE harness.erasure_backfill_plans p
            SET doc = jsonb_set(p.doc, '{orgs}', COALESCE((SELECT jsonb_agg(o) FROM jsonb_array_elements(p.doc->'orgs') o
              WHERE o->>'orgId' <> $1), '[]'::jsonb))
          WHERE p.doc->'orgs' @> jsonb_build_array(jsonb_build_object('orgId', $1::text))`,
        [orgId],
      );
      return (own.rowCount ?? 0) + (shared.rowCount ?? 0);
    });
  }
}
