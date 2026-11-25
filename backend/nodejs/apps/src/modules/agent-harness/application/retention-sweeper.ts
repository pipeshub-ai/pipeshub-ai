import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import type { CostEraser } from '../ports/cost-ledger';
import { TERMINAL_RUN_STATUSES } from '../domain/execution';
import {
  AGENT_DELETE_CONFIRM_AFTER_MS,
  AGENT_DELETE_WITHDRAW_AFTER_MS,
  ERASURE_ALERT_AFTER_FAILURES,
  erasureBackoffMs,
  type ErasureRequest,
} from '../domain/session-erasure';
import {
  UNSENT_ATTACHMENT_GRACE_MS,
  UNSENT_ATTACHMENT_PAGES,
  UNSENT_ATTACHMENT_SWEEP_MS,
  ABANDONED_FORK_AFTER_MS,
  DISCARDED_DRAFT_GRACE_MS,
  PAYLOAD_GC_GRACE_MS,
  RECEIPT_TTL_MS,
  RETENTION_SWEEP_BATCH,
  RETENTION_SWEEP_INTERVAL_MS,
  RETENTION_SWEEP_LEASE_MS,
  RETENTION_SWEEP_LEASE_NAME,
  retentionCutoff,
} from '../domain/session-retention';
import {
  AgentDeletionLookup,
  AgentDeletionState,
  AgentErasureQueue,
  PendingAgentErasure,
} from '../ports/agent-erasures';
import { LeaderLease } from '../ports/leader-lease';
import type {
  SandboxSnapshotIndex,
  SnapshotTtlSource,
  StoredObjectPurger,
} from '../ports/sandbox-snapshots';
import { snapshotCutoff } from '../domain/snapshot-ttl';
import { AbandonedFork, AbandonedForks } from '../ports/session-forks';
import { MemoryEraser } from '../ports/memory-store';
import {
  ArtifactEraser,
  AttachmentCarriers,
  AttachmentEraser,
  SessionErasureQueue,
  SessionRunLookup,
  UnsentAttachments,
} from '../ports/session-erasure';
import { ATTACHMENT_ERASE_PAGE } from '../domain/session-erasure';
import {
  ConversationRetention,
  ErasureRequestCursor,
  RetentionPass,
  RetentionStore,
  SessionRetentionSettings,
} from '../ports/session-retention';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { detachedInterval } from '../../../libs/context/orgContext';

/** The retention sweep's per-org work, bound to the org its cross-org read found. */
const RETENTION_JOB = 'agent-harness.retention';

export interface RetentionSweepReport {
  payloads: number;
  receipts: number;
  admissionReceipts: number;
  expiredSessions: number;
  /** Erasures asked for by a delete whose enqueue was lost, enqueued again. */
  requeued: number;
  discardedDrafts: number;
  agentErasures: number;
  /** Forks whose conversation never committed, sent to erasure (CLEANUP-G4). */
  abandonedForks: number;
  /** Chat attachments no message carried a day after their upload, erased (CLEANUP-G5). */
  unsentAttachments: number;
  /** Steps of org and user deletions finished (CLEANUP-G1, G2). */
  principalErasureSteps: number;
  /** Engine sandbox snapshots deleted past their org's TTL (OWD-12). */
  expiredSnapshots: number;
  /** Spend records the cost eraser's sweep moved (`CostEraser.sweep`; none without one). */
  costLedger: number;
}

/** OWD-12: the stored engine sandbox snapshots, each org's TTL, and how a stored object is deleted. */
export interface SnapshotExpiry {
  index: SandboxSnapshotIndex;
  ttl: SnapshotTtlSource;
  objects: StoredObjectPurger;
}

/**
 * Agent erasures after a deletion (H4, H5): failed ones retried until done;
 * intents whose delete never reported back confirmed with the query service
 * first, and withdrawn when the agent was not deleted.
 */
export interface AgentErasureRetry {
  queue: AgentErasureQueue;
  memories: Pick<MemoryEraser, 'eraseAgent'>;
  deletions: AgentDeletionLookup;
}

export interface RetentionSweeperDependencies<Tx> {
  store: RetentionStore;
  settings: SessionRetentionSettings;
  /** A pass after the orgs' own periods (`RetentionPass`); absent: none. */
  extraPass?: RetentionPass;
  conversations: ConversationRetention<Tx>;
  erasures: SessionErasureQueue<Tx>;
  runs: SessionRunLookup;
  /** Absent: no artifact registry to collect discarded drafts from. */
  artifacts: ArtifactEraser | null;
  /** Absent: nothing to retry (no agent deletion path composed). */
  agentErasures?: AgentErasureRetry | null;
  /** Absent: no "continue privately" forks in this deployment. */
  abandonedForks?: AbandonedForks<Tx> | null;
  /** Org and user deletions (CLEANUP-G1, G2); absent: none run here. */
  principalErasures?: { tick(now: number): Promise<number> } | null;
  /** Absent: no snapshot index or storage composed, so no snapshot is deleted here. */
  snapshots?: SnapshotExpiry | null;
  /** What a lost commit left of the month's spend records; absent: none are kept. */
  costLedger?: Pick<CostEraser, 'sweep'> | null;
  /** Absent: no chat attachments to collect (no query service composed). */
  unsentAttachments?: {
    uploads: UnsentAttachments;
    carriers: AttachmentCarriers;
    eraser: AttachmentEraser;
  } | null;
  /** `orgId`: the org the transaction commits for (a sweep has none bound). */
  transaction: <T>(
    work: (tx: Tx) => Promise<T>,
    scope?: { orgId: string },
  ) => Promise<T>;
  lease: LeaderLease;
  holderId: string;
  logger?: HarnessLogger;
}

const errorType = (error: unknown): string =>
  error instanceof Error ? error.name : typeof error;

/**
 * T5 (P1.10) on one replica at a time: unreferenced payloads, receipt TTLs,
 * the orgs' retention periods, erasures whose enqueue was lost, and discarded
 * drafts. Each part runs on its own, so one failing does not hold back the
 * others; every part takes a bounded batch per pass. Conversations deleted
 * before erasure existed are not its business: only the operator's backfill
 * erases them (E7B).
 */
export class RetentionSweeper<Tx> {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  /** Where the lost-enqueue pass continues; per process, so a restart rescans once. */
  private requeueAfter: ErasureRequestCursor | null = null;
  /** When this process next asks for waiting uploads; a restart asks at once. */
  private unsentAttachmentsDueAt = 0;
  private readonly logger: HarnessLogger;

  constructor(private readonly deps: RetentionSweeperDependencies<Tx>) {
    this.logger = deps.logger ?? silentHarnessLogger;
  }

  start(intervalMs = RETENTION_SWEEP_INTERVAL_MS): void {
    if (this.timer) return;
    this.timer = detachedInterval(() => {
      void this.tick();
    }, intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    while (this.running) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    await this.deps.lease
      .release(RETENTION_SWEEP_LEASE_NAME, this.deps.holderId)
      .catch(() => undefined);
  }

  async tick(): Promise<RetentionSweepReport | null> {
    if (this.running) return null;
    this.running = true;
    try {
      if (
        !(await this.deps.lease.tryAcquire(
          RETENTION_SWEEP_LEASE_NAME,
          this.deps.holderId,
          RETENTION_SWEEP_LEASE_MS,
        ))
      ) {
        return null;
      }
      const now = await this.deps.store.now();
      const { store } = this.deps;
      const report: RetentionSweepReport = {
        payloads: await this.part('payloads', () =>
          store.collectPayloads(
            now - PAYLOAD_GC_GRACE_MS,
            RETENTION_SWEEP_BATCH,
          ),
        ),
        receipts: await this.part('receipts', () =>
          store.expireCommandReceipts(
            now - RECEIPT_TTL_MS,
            RETENTION_SWEEP_BATCH,
          ),
        ),
        admissionReceipts: await this.part('admission_receipts', () =>
          store.expireAdmissionReceipts(
            now - RECEIPT_TTL_MS,
            RETENTION_SWEEP_BATCH,
          ),
        ),
        expiredSessions: await this.part('retention', () => this.expire(now)),
        requeued: await this.part('requeue', () => this.requeue()),
        discardedDrafts: await this.part('discarded_drafts', () =>
          this.collectDrafts(now),
        ),
        agentErasures: await this.part('agent_erasures', () =>
          this.retryAgentErasures(now),
        ),
        abandonedForks: await this.part('abandoned_forks', () =>
          this.eraseAbandonedForks(now),
        ),
        unsentAttachments: await this.part('unsent_attachments', () =>
          this.collectUnsentAttachments(now),
        ),
        principalErasureSteps: await this.part(
          'principal_erasures',
          () => this.deps.principalErasures?.tick(now) ?? Promise.resolve(0),
        ),
        expiredSnapshots: await this.part('expired_snapshots', () =>
          this.expireSnapshots(now),
        ),
        costLedger: await this.part(
          'cost_ledger',
          () => this.deps.costLedger?.sweep(now) ?? Promise.resolve(0),
        ),
      };
      if (Object.values(report).some((count) => count > 0)) {
        this.logger.info(
          'retention.swept',
          harnessLogMeta('retention.swept', { ...report }),
        );
      }
      return report;
    } catch (error) {
      this.logger.error(
        'retention.tick_failed',
        harnessLogMeta('retention.tick_failed', {
          errorType: errorType(error),
        }),
      );
      return null;
    } finally {
      this.running = false;
    }
  }

  private async part(
    name: string,
    work: () => Promise<number>,
  ): Promise<number> {
    try {
      return await work();
    } catch (error) {
      this.logger.warn(
        'retention.part_failed',
        harnessLogMeta('retention.part_failed', {
          part: name,
          errorType: errorType(error),
        }),
      );
      return 0;
    }
  }

  /**
   * Engine sandbox snapshots taken before their org's TTL (OWD-12): the
   * stored object first, then its row, so a failed delete is tried again on
   * the next pass. A run never restores one past its TTL meanwhile (the
   * worker and admission check the age themselves).
   */
  private async expireSnapshots(now: number): Promise<number> {
    const snapshots = this.deps.snapshots;
    if (!snapshots) return 0;
    let deleted = 0;
    for (const orgId of await snapshots.index.orgs()) {
      await forEnumeratedOrg(orgId, RETENTION_JOB, async () => {
        const days = await snapshots.ttl.days(orgId);
        const before = snapshotCutoff(now, days);
        const purged: string[] = [];
        for (const snapshot of await snapshots.index.takenBefore(
          orgId,
          before,
          RETENTION_SWEEP_BATCH,
        )) {
          try {
            await snapshots.objects.purge(orgId, snapshot.objectId);
            purged.push(snapshot.objectId);
          } catch (error) {
            this.logger.warn(
              'retention.snapshot_purge_failed',
              harnessLogMeta('retention.snapshot_purge_failed', {
                orgId,
                errorType: errorType(error),
              }),
            );
          }
        }
        const forgotten = await snapshots.index.forget(orgId, purged, before);
        if (forgotten > 0) {
          deleted += forgotten;
          this.logger.info(
            'retention.snapshots_expired',
            harnessLogMeta('retention.snapshots_expired', {
              orgId,
              snapshots: forgotten,
              ttlDays: days,
            }),
          );
        }
      });
    }
    return deleted;
  }

  /** Conversations idle past their org's period, then the extra pass's; one with a run still going waits. */
  private async expire(now: number): Promise<number> {
    const periods = await this.deps.settings.all();
    let expired = 0;
    for (const { orgId, days } of periods) {
      await forEnumeratedOrg(orgId, RETENTION_JOB, async () => {
        const before = retentionCutoff(now, days);
        for (const sessionId of await this.deps.conversations.idleSince(
          orgId,
          before,
          RETENTION_SWEEP_BATCH,
        )) {
          if (await this.expireIdle(orgId, sessionId, before, { days })) {
            expired += 1;
          }
        }
      });
    }
    const pass = this.deps.extraPass;
    if (pass === undefined) return expired;
    return (
      expired +
      (await pass.run(now, periods, (orgId, sessionId, before, why) =>
        forEnumeratedOrg(orgId, RETENTION_JOB, () =>
          this.expireIdle(orgId, sessionId, before, why),
        ),
      ))
    );
  }

  /** `expireOne` unless a run of the conversation is still going. */
  private async expireIdle(
    orgId: string,
    sessionId: string,
    before: number,
    why: { days: number; detail?: Readonly<Record<string, string>> },
  ): Promise<boolean> {
    const runs = await this.deps.runs.runsOf(orgId, sessionId);
    if (runs.some((run) => !TERMINAL_RUN_STATUSES.has(run.status)))
      return false;
    return this.expireOne(orgId, sessionId, before, why);
  }

  /** Expires one conversation idle since before `before` and enqueues its erasure, in one transaction. */
  private async expireOne(
    orgId: string,
    sessionId: string,
    before: number,
    why: { days: number; detail?: Readonly<Record<string, string>> },
  ): Promise<boolean> {
    const done = await this.deps.transaction(
      async (tx) => {
        if (
          !(await this.deps.conversations.expire(orgId, sessionId, before, tx))
        ) {
          return false;
        }
        await this.deps.erasures.enqueue(
          { orgId, sessionId, reason: 'retention', by: null },
          tx,
        );
        return true;
      },
      { orgId },
    );
    if (done) {
      this.logger.info(
        'retention.expired',
        harnessLogMeta('retention.expired', {
          orgId,
          sessionId,
          ...why.detail,
          days: why.days,
        }),
      );
    }
    return done;
  }

  /**
   * Uploads the query service still marks as waiting for a message a day
   * after they were made (CLEANUP-G5): one a message carries is marked sent,
   * one none carries is erased. Only a message decides, so an upload whose
   * message was written without marking it is never lost.
   */
  private async collectUnsentAttachments(now: number): Promise<number> {
    const unsent = this.deps.unsentAttachments;
    if (!unsent || now < this.unsentAttachmentsDueAt) return 0;
    this.unsentAttachmentsDueAt = now + UNSENT_ATTACHMENT_SWEEP_MS;
    const before = now - UNSENT_ATTACHMENT_GRACE_MS;
    let erased = 0;
    for (const orgId of await unsent.carriers.orgs()) {
      await forEnumeratedOrg(orgId, RETENTION_JOB, async () => {
        let after: string | null = null;
        for (let page = 0; page < UNSENT_ATTACHMENT_PAGES; page += 1) {
          const { uploads, remaining, next } = await unsent.uploads.unsent(
            orgId,
            before,
            ATTACHMENT_ERASE_PAGE,
            after,
          );
          const carried = await unsent.carriers.carriedBy(
            orgId,
            uploads.map((upload) => upload.recordId),
          );
          const byConversation = new Map<string, string[]>();
          for (const [recordId, conversationId] of carried) {
            byConversation.set(conversationId, [
              ...(byConversation.get(conversationId) ?? []),
              recordId,
            ]);
          }
          for (const [conversationId, recordIds] of byConversation) {
            await unsent.uploads.markSent(orgId, conversationId, recordIds);
          }
          const abandoned = uploads.filter(
            (upload) => !carried.has(upload.recordId),
          );
          if (abandoned.length > 0) {
            erased += (
              await unsent.eraser.eraseAttachments(
                orgId,
                abandoned.map(({ recordId, ownerId }) => ({
                  recordId,
                  ownerId,
                })),
              )
            ).attachments;
          }
          if (!remaining || next === null) break;
          after = next;
        }
      });
    }
    return erased;
  }

  /**
   * Forks whose conversation never committed (CLEANUP-G4): the records go and
   * the forks' sessions are erased, in one transaction per org. No conversation
   * names them, so this is the only way their copies are ever erased.
   */
  private async eraseAbandonedForks(now: number): Promise<number> {
    const forks = this.deps.abandonedForks;
    if (!forks) return 0;
    const before = now - ABANDONED_FORK_AFTER_MS;
    const byOrg = new Map<string, AbandonedFork[]>();
    for (const fork of await forks.list(before, RETENTION_SWEEP_BATCH)) {
      byOrg.set(fork.orgId, [...(byOrg.get(fork.orgId) ?? []), fork]);
    }
    let erased = 0;
    for (const [orgId, listed] of byOrg) {
      const dropped = await forEnumeratedOrg(orgId, RETENTION_JOB, () =>
        this.deps.transaction(
          async (tx) => {
            const gone = await forks.dropMany(orgId, listed, before, tx);
            if (gone.length > 0) {
              await this.deps.erasures.enqueueMany(
                gone.map((fork) => ({
                  orgId,
                  sessionId: fork.forkSessionId,
                  reason: 'retention' as const,
                  by: null,
                })),
                tx,
              );
            }
            return gone;
          },
          { orgId },
        ),
      );
      erased += dropped.length;
      for (const fork of dropped) {
        this.logger.info(
          'retention.fork_abandoned',
          harnessLogMeta('retention.fork_abandoned', {
            orgId,
            sessionId: fork.forkSessionId,
          }),
        );
      }
    }
    return erased;
  }

  /**
   * Deleted conversations whose delete asked for erasure but that have no job:
   * the delete's enqueue was lost (a deployment without transactions). The
   * enqueue is idempotent, so a session whose job exists is left as it is.
   */
  private async requeue(): Promise<number> {
    const awaiting = await this.deps.conversations.awaitingErasure(
      this.requeueAfter,
      RETENTION_SWEEP_BATCH,
    );
    const byOrg = new Map<string, ErasureRequest[]>();
    for (const { orgId, sessionId, by } of awaiting) {
      const requests = byOrg.get(orgId) ?? [];
      requests.push({
        orgId,
        sessionId,
        reason: by === null ? 'retention' : 'user_delete',
        by,
      });
      byOrg.set(orgId, requests);
    }
    let enqueued = 0;
    for (const [orgId, requests] of byOrg) {
      enqueued += await forEnumeratedOrg(orgId, RETENTION_JOB, () =>
        this.deps.erasures.enqueueMany(requests),
      );
    }
    const last = awaiting[awaiting.length - 1];
    this.requeueAfter =
      awaiting.length < RETENTION_SWEEP_BATCH || last === undefined
        ? null
        : { requestedAt: last.requestedAt, sessionId: last.sessionId };
    if (enqueued > 0) {
      this.logger.info(
        'retention.requeued',
        harnessLogMeta('retention.requeued', { enqueued }),
      );
    }
    return enqueued;
  }

  /**
   * Each due agent erasure once; an unconfirmed one only after the query
   * service says the agent is deleted. A failure is due again after the
   * erasure backoff, never given up.
   */
  private async retryAgentErasures(now: number): Promise<number> {
    const retry = this.deps.agentErasures;
    if (!retry) return 0;
    let settled = 0;
    for (const entry of await retry.queue.due(now, RETENTION_SWEEP_BATCH)) {
      if (
        await forEnumeratedOrg(entry.orgId, RETENTION_JOB, () =>
          this.eraseAgent(retry, entry, now),
        )
      )
        settled += 1;
    }
    return settled;
  }

  /** True when the agent's memories were erased and the entry settled. */
  private async eraseAgent(
    retry: AgentErasureRetry,
    entry: PendingAgentErasure,
    now: number,
  ): Promise<boolean> {
    const ids = { orgId: entry.orgId, agentKey: entry.agentKey };
    try {
      if (!entry.confirmed) {
        const state = await retry.deletions.state(entry.orgId, entry.agentKey);
        if (state !== 'deleted') {
          await this.notDeleted(retry.queue, entry, state, now);
          return false;
        }
      }
      const erased = await retry.memories.eraseAgent(
        entry.orgId,
        entry.agentKey,
      );
      await retry.queue.settle(entry.orgId, entry.agentKey);
      this.logger.info(
        'agent_erasure.retried',
        harnessLogMeta('agent_erasure.retried', {
          ...ids,
          erased,
          failures: entry.failures,
          confirmedBy: entry.confirmed ? 'delete' : 'lookup',
        }),
      );
      return true;
    } catch (error) {
      const failures = entry.failures + 1;
      const fields = harnessLogMeta('agent_erasure.retry_failed', {
        ...ids,
        failures,
        errorType: errorType(error),
        retryInMs: erasureBackoffMs(failures),
      });
      if (failures >= ERASURE_ALERT_AFTER_FAILURES) {
        this.logger.error('agent_erasure.retry_failed', fields);
      } else {
        this.logger.warn('agent_erasure.retry_failed', fields);
      }
      await retry.queue.retryLater(
        entry,
        now + erasureBackoffMs(failures),
        errorType(error),
      );
      return false;
    }
  }

  /** The agent is live or unknown: looked at again, and withdrawn once its delete is surely over. */
  private async notDeleted(
    queue: AgentErasureQueue,
    entry: PendingAgentErasure,
    state: Exclude<AgentDeletionState, 'deleted'>,
    now: number,
  ): Promise<void> {
    const ids = { orgId: entry.orgId, agentKey: entry.agentKey, state };
    if (now - entry.requestedAt < AGENT_DELETE_WITHDRAW_AFTER_MS) {
      await queue.postpone(entry, now + AGENT_DELETE_CONFIRM_AFTER_MS);
      this.logger.info(
        'agent_erasure.unconfirmed',
        harnessLogMeta('agent_erasure.unconfirmed', {
          ...ids,
          retryInMs: AGENT_DELETE_CONFIRM_AFTER_MS,
        }),
      );
      return;
    }
    if (await queue.withdraw(entry)) {
      this.logger.info(
        'agent_erasure.withdrawn',
        harnessLogMeta('agent_erasure.withdrawn', ids),
      );
    }
  }

  private async collectDrafts(now: number): Promise<number> {
    if (this.deps.artifacts === null) return 0;
    let collected = 0;
    for (const orgId of await this.deps.store.harnessOrgs()) {
      const artifacts = this.deps.artifacts;
      const report = await forEnumeratedOrg(orgId, RETENTION_JOB, () =>
        artifacts.collectDiscarded(orgId, now - DISCARDED_DRAFT_GRACE_MS),
      );
      collected += report.artifacts;
    }
    return collected;
  }
}
