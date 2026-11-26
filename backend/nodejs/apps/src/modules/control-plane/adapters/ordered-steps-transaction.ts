import { randomUUID } from 'node:crypto';
import { changeEventOf, ConfigChanged } from '../domain/config-change';
import { ControlPlaneUnavailableError } from '../domain/errors';
import type {
  ChangeOutbox,
  ConfigAuditLog,
  ConfigAuditReader,
} from '../ports/config-audit-log';
import { ControlPlaneLog, silentControlPlaneLog } from '../ports/log';
import type {
  Committed,
  ControlPlaneTransaction,
  WriteContext,
  Work,
} from '../ports/transaction';
import {
  committedChange,
  concurrentlyChanged,
  DEFAULT_RETRY,
  isRetryable,
  logCommitted,
  RetryPolicy,
  StagedCpTx,
} from './staged-transaction';

/** What a writer that took a lease over found: the org's revision and a dead writer's intent. */
export interface AcquiredLease {
  revision: number;
  stale: ConfigChanged | null;
}

/**
 * The per-org head and its write lease on a store without transactions.
 * Every method but `acquire` is conditional on the caller's token, so a
 * writer whose lease was taken over can no longer move the head.
 */
export interface OrgHeadLeases {
  /** Null while another writer holds a live lease. */
  acquire(
    orgId: string,
    token: string,
    now: number,
    until: number,
  ): Promise<AcquiredLease | null>;
  /** False when the lease is no longer the caller's. */
  extend(orgId: string, token: string, until: number): Promise<boolean>;
  recordIntent(
    orgId: string,
    token: string,
    intent: ConfigChanged,
  ): Promise<boolean>;
  /** Sets the revision, keeps the lease, clears the intent. */
  advance(orgId: string, token: string, revision: number): Promise<void>;
  /** Sets the revision and frees the lease. */
  finalize(orgId: string, token: string, revision: number): Promise<void>;
  /** Frees the lease; the revision and any intent stay (`keepIntent`) for the next writer to repair. */
  release(orgId: string, token: string, keepIntent: boolean): Promise<void>;
}

/** Completes a pending intent: its audit entry and change event, once. */
export class ControlPlaneRepair {
  constructor(
    private readonly audit: ConfigAuditLog & ConfigAuditReader,
    private readonly outbox: ChangeOutbox | undefined,
    private readonly log: ControlPlaneLog,
  ) {}

  async complete(intent: ConfigChanged): Promise<void> {
    if (await this.audit.recorded(intent.orgId, intent.headRevision)) return;
    const repaired: ConfigChanged = { ...intent, repaired: true };
    const tx = new StagedCpTx(
      intent.orgId,
      intent.headRevision,
      undefined,
      (write) => write(),
    );
    await this.audit.record(tx, repaired);
    await this.outbox?.enqueue(tx, changeEventOf(repaired));
    await this.outbox?.committed(tx);
    this.log.warn('cp.repair.completed', {
      event: 'cp.repair.completed',
      orgId: intent.orgId,
      objectType: intent.object.type,
      objectId: intent.object.id,
      headRevision: intent.headRevision,
    });
  }
}

export interface OrderedStepsTransactionOptions {
  audit: ConfigAuditLog & ConfigAuditReader;
  heads: OrgHeadLeases;
  outbox?: ChangeOutbox;
  log?: ControlPlaneLog;
  retry?: RetryPolicy;
  leaseMs?: number;
  now?: () => number;
  newToken?: () => string;
}

const DEFAULT_LEASE_MS = 30_000;

/**
 * Standalone Mongo (02-lld §6.4): the same steps in order, each keyed by the
 * org head revision. The intent is recorded before the effects and the audit
 * entry after them, so a writer that stops in between leaves an intent the
 * next writer in the org completes. Degraded: an audit failure after the
 * effects fails the call but the change stays applied until the repair
 * records it.
 */
export class OrderedStepsTransaction implements ControlPlaneTransaction {
  private readonly log: ControlPlaneLog;
  private readonly retry: RetryPolicy;
  private readonly leaseMs: number;
  private readonly now: () => number;
  private readonly newToken: () => string;
  private readonly repair: ControlPlaneRepair;

  constructor(private readonly options: OrderedStepsTransactionOptions) {
    this.log = options.log ?? silentControlPlaneLog;
    this.retry = options.retry ?? DEFAULT_RETRY;
    this.leaseMs = options.leaseMs ?? DEFAULT_LEASE_MS;
    this.now = options.now ?? Date.now;
    this.newToken = options.newToken ?? randomUUID;
    this.repair = new ControlPlaneRepair(
      options.audit,
      options.outbox,
      this.log,
    );
  }

  async run<T>(ctx: WriteContext, work: Work<T>): Promise<Committed<T>> {
    const { heads } = this.options;
    const token = this.newToken();
    const revision = await this.acquire(ctx.orgId, token);
    const headRevision = revision + 1;
    for (let attempt = 1; ; attempt += 1) {
      const tx = new StagedCpTx(ctx.orgId, headRevision, undefined, (write) =>
        write(),
      );
      let changed: ConfigChanged;
      let result: T;
      try {
        const planned = await work(tx);
        result = planned.result;
        changed = committedChange(ctx, headRevision, planned.change);
        if (!(await heads.recordIntent(ctx.orgId, token, changed))) {
          throw new ControlPlaneUnavailableError(
            'The control-plane write lease was lost',
          );
        }
        await tx.applyEffects();
      } catch (error) {
        if (tx.appliedAny) {
          await heads.release(ctx.orgId, token, true);
          throw new ControlPlaneUnavailableError(
            'The change was applied in part; it is recorded at the next write',
            { cause: error },
          );
        }
        if (isRetryable(error) && attempt < this.retry.attempts) {
          await this.retry.pause(attempt);
          continue;
        }
        await heads.release(ctx.orgId, token, false);
        throw isRetryable(error) ? concurrentlyChanged(error) : error;
      }
      await this.recordApplied(ctx.orgId, token, changed);
      return { result, headRevision };
    }
  }

  private async acquire(orgId: string, token: string): Promise<number> {
    const { heads } = this.options;
    for (let attempt = 1; ; attempt += 1) {
      const now = this.now();
      const lease = await heads.acquire(orgId, token, now, now + this.leaseMs);
      if (lease !== null) {
        if (lease.stale === null) return lease.revision;
        await this.repair.complete(lease.stale);
        await heads.advance(orgId, token, lease.stale.headRevision);
        return lease.stale.headRevision;
      }
      if (attempt >= this.retry.attempts) {
        throw new ControlPlaneUnavailableError(
          'Another control-plane write in this org is in progress',
        );
      }
      await this.retry.pause(attempt);
    }
  }

  private async recordApplied(
    orgId: string,
    token: string,
    changed: ConfigChanged,
  ): Promise<void> {
    const { heads, audit, outbox } = this.options;
    if (!(await heads.extend(orgId, token, this.now() + this.leaseMs))) {
      throw new ControlPlaneUnavailableError(
        'The change was applied; another writer took the lease over and records it',
      );
    }
    const tx = new StagedCpTx(orgId, changed.headRevision, undefined, (write) =>
      write(),
    );
    try {
      await audit.record(tx, changed);
      await outbox?.enqueue(tx, changeEventOf(changed));
    } catch (error) {
      await heads.release(orgId, token, true);
      throw new ControlPlaneUnavailableError(
        'The change was applied; its audit entry is recorded at the next write',
        { cause: error },
      );
    }
    await heads.finalize(orgId, token, changed.headRevision);
    logCommitted(this.log, changed);
    await outbox?.committed(tx);
  }
}
