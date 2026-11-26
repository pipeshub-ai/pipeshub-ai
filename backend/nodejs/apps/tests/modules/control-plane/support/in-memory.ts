/* eslint-disable @typescript-eslint/require-await -- in-memory adapters implement asynchronous ports without I/O. */
/**
 * In-memory adapters of the control-plane ports (R3 M14: test support, not
 * production code): the transaction, the ordered-steps head leases and the
 * change outbox. Each passes the same contract suite as its Mongo twin.
 */
import { AfterCommitPublisher } from '../../../../src/libs/utils/after-commit';
import {
  ChangeEvent,
  ChangeOutbox,
  Committed,
  ConfigAuditLog,
  ConfigChanged,
  ControlPlaneLog,
  ControlPlaneTransaction,
  CpTx,
  WriteContext,
  Work,
} from '../../../../src/modules/control-plane';
import {
  AcquiredLease,
  OrgHeadLeases,
} from '../../../../src/modules/control-plane/adapters/ordered-steps-transaction';
import {
  committedChange,
  concurrentlyChanged,
  isRetryable,
  logCommitted,
  RetryPolicy,
  StagedCpTx,
  stageAndApply,
  unaudited,
} from '../../../../src/modules/control-plane/adapters/staged-transaction';
import { silentControlPlaneLog } from '../../../../src/modules/control-plane/ports/log';
import { InMemoryAuditChainStore } from '../../../../src/modules/agent-harness/adapters/in-memory-audit';
import { ChainConfigAuditLog } from '../../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { AuditRecord } from '../../../../src/modules/agent-harness/domain/audit';

export const NO_PAUSE: RetryPolicy = { attempts: 8, pause: async () => undefined };

/** Serialises async work per key; other keys run freely. */
class KeyedLock {
  private readonly tails = new Map<string, Promise<void>>();

  async with<T>(key: string, work: () => Promise<T>): Promise<T> {
    const before = this.tails.get(key) ?? Promise.resolve();
    let release!: () => void;
    const mine = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = before.then(() => mine);
    this.tails.set(key, tail);
    await before;
    try {
      return await work();
    } finally {
      release();
      if (this.tails.get(key) === tail) this.tails.delete(key);
    }
  }
}

export interface InMemoryTransactionOptions {
  audit: ConfigAuditLog;
  outbox?: ChangeOutbox;
  log?: ControlPlaneLog;
  retry?: RetryPolicy;
  /** Fault injection: runs between the effects and the commit. */
  beforeCommit?: () => void;
}

/** Writes enlisted in a transaction run at its commit; a rollback drops them. */
export class InMemoryControlPlaneTransaction implements ControlPlaneTransaction {
  readonly heads = new Map<string, number>();
  private readonly lock = new KeyedLock();

  constructor(private readonly options: InMemoryTransactionOptions) {}

  run<T>(ctx: WriteContext, work: Work<T>): Promise<Committed<T>> {
    return this.lock.with(ctx.orgId, () => this.runLocked(ctx, work));
  }

  private async runLocked<T>(ctx: WriteContext, work: Work<T>): Promise<Committed<T>> {
    const log = this.options.log ?? silentControlPlaneLog;
    const retry = this.options.retry ?? NO_PAUSE;
    for (let attempt = 1; ; attempt += 1) {
      const headRevision = (this.heads.get(ctx.orgId) ?? 0) + 1;
      const enlisted: Array<() => Promise<void>> = [];
      const tx = new StagedCpTx(ctx.orgId, headRevision, undefined, async (write) => {
        enlisted.push(write);
      });
      let changed: ConfigChanged | null = null;
      try {
        const { result, change } = await work(tx);
        changed = committedChange(ctx, headRevision, change);
        await stageAndApply(tx, changed, this.options.audit, this.options.outbox);
        this.options.beforeCommit?.();
        for (const write of enlisted) await write();
        this.heads.set(ctx.orgId, headRevision);
        logCommitted(log, changed);
        await this.options.outbox?.committed(tx);
        return { result, headRevision };
      } catch (error) {
        if (tx.appliedAny) throw unaudited(log, changed, ctx.orgId, headRevision, error);
        if (!isRetryable(error)) throw error;
        if (attempt >= retry.attempts) throw concurrentlyChanged(error);
        await retry.pause(attempt);
      }
    }
  }
}

/** `cpOrgHeads` with leases, as `MongoOrgHeadLeases` keeps them. */
export class InMemoryOrgHeadLeases implements OrgHeadLeases {
  readonly rows = new Map<
    string,
    { revision: number; lease: { token: string; until: number; intent: ConfigChanged | null } | null }
  >();

  async acquire(orgId: string, token: string, now: number, until: number): Promise<AcquiredLease | null> {
    const row = this.rows.get(orgId) ?? { revision: 0, lease: null };
    if (row.lease !== null && row.lease.until > now) return null;
    const stale = row.lease?.intent ?? null;
    this.rows.set(orgId, { revision: row.revision, lease: { token, until, intent: null } });
    return { revision: row.revision, stale: stale === null ? null : structuredClone(stale) };
  }

  private held(orgId: string, token: string) {
    const row = this.rows.get(orgId);
    return row?.lease?.token === token ? row : null;
  }

  async extend(orgId: string, token: string, until: number): Promise<boolean> {
    const row = this.held(orgId, token);
    if (row === null || row.lease === null) return false;
    row.lease.until = until;
    return true;
  }

  async recordIntent(orgId: string, token: string, intent: ConfigChanged): Promise<boolean> {
    const row = this.held(orgId, token);
    if (row === null || row.lease === null) return false;
    row.lease.intent = structuredClone(intent);
    return true;
  }

  async advance(orgId: string, token: string, revision: number): Promise<void> {
    const row = this.held(orgId, token);
    if (row === null || row.lease === null) return;
    row.revision = revision;
    row.lease.intent = null;
  }

  async finalize(orgId: string, token: string, revision: number): Promise<void> {
    const row = this.held(orgId, token);
    if (row === null) return;
    row.revision = revision;
    row.lease = null;
  }

  async release(orgId: string, token: string, keepIntent: boolean): Promise<void> {
    const row = this.held(orgId, token);
    if (row === null || row.lease === null) return;
    if (keepIntent) row.lease.until = 0;
    else row.lease = null;
  }
}

/** Rows land at the commit; `announce` hears each committed event once the transaction says it committed. */
export class InMemoryChangeOutbox implements ChangeOutbox {
  readonly events: ChangeEvent[] = [];
  private readonly commits = new AfterCommitPublisher<CpTx>();

  constructor(private readonly announce: (event: ChangeEvent) => void = () => undefined) {}

  enqueue(tx: CpTx, event: ChangeEvent): Promise<void> {
    this.commits.defer(tx, async () => {
      this.announce(structuredClone(event));
    });
    return tx.enlist(async () => {
      this.events.push(structuredClone(event));
    });
  }

  committed(tx: CpTx): Promise<void> {
    return this.commits.committed(tx);
  }
}

/** A `ConfigAuditLog` whose store is down. */
export class FailingConfigAuditLog implements ConfigAuditLog {
  async record(): Promise<void> {
    throw new Error('audit store unavailable');
  }
}

/** A promise the test resolves by hand: no sleeps. */
export class Gate {
  readonly opened: Promise<void>;
  open!: () => void;

  constructor() {
    this.opened = new Promise((resolve) => {
      this.open = resolve;
    });
  }
}

export class RecordingLog implements ControlPlaneLog {
  readonly lines: Array<[string, string, Record<string, unknown> | undefined]> = [];
  info(message: string, meta?: Record<string, unknown>): void {
    this.lines.push(['info', message, meta]);
  }
  warn(message: string, meta?: Record<string, unknown>): void {
    this.lines.push(['warn', message, meta]);
  }
  error(message: string, meta?: Record<string, unknown>): void {
    this.lines.push(['error', message, meta]);
  }
}

/** An in-memory transaction whose audit entries land on an in-memory chain and whose change events in an outbox, both readable. */
export function auditedControlPlane(chain = new InMemoryAuditChainStore()): {
  transaction: InMemoryControlPlaneTransaction;
  chain: InMemoryAuditChainStore;
  outbox: InMemoryChangeOutbox;
  configEntries(orgId: string): AuditRecord[];
} {
  const outbox = new InMemoryChangeOutbox();
  return {
    transaction: new InMemoryControlPlaneTransaction({ audit: new ChainConfigAuditLog(chain), outbox }),
    chain,
    outbox,
    configEntries: (orgId) =>
      (chain.records.get(orgId) ?? []).filter((record) => record.streamId === `cp:${orgId}`),
  };
}

/** For tests that write config but do not look at its audit. */
export const testControlPlane = (): ControlPlaneTransaction => auditedControlPlane().transaction;
