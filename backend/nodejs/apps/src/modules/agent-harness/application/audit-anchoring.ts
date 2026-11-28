import {
  AUDIT_ANCHOR_BATCH,
  AUDIT_ANCHOR_INTERVAL_MS,
  AUDIT_ANCHOR_LEASE_MS,
  AUDIT_ANCHOR_LEASE_NAME,
  AUDIT_ANCHOR_RENEW_MS,
  AUDIT_LINK_STALE_MS,
  AuditVerification,
  unlinkedAgeMs,
  verifyAuditChain,
} from '../domain/audit';
import { AuditRecord } from '../domain/audit';
import { AuditAnchorSink, AuditChainStore } from '../ports/audit';
import { LeaderLease } from '../ports/leader-lease';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import {
  detachedInterval,
  forEnumeratedOrg,
} from '../../../libs/context/orgContext';

const VERIFY_PAGE = 500;

type ChainReader = Pick<
  AuditChainStore<unknown>,
  'read' | 'head' | 'unanchored' | 'markAnchored' | 'unlinked'
>;

export interface AuditAnchorServiceOptions {
  chain: ChainReader;
  sink: AuditAnchorSink;
  lease: LeaderLease;
  holderId: string;
  logger?: HarnessLogger;
  now?: () => number;
}

/**
 * Writes each org's chain head to the anchor sink when it grew (one replica
 * at a time, elected through the lease). An anchor is written before the
 * head is marked, so a crash between the two re-anchors the same position.
 *
 * The lease is renewed every `AUDIT_ANCHOR_RENEW_MS` and anchoring runs on
 * the leader once per interval, and at once when it takes the lease over, so
 * a new leader seals what its predecessor left without waiting an interval.
 */
export class AuditAnchorService {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private beating: Promise<number> | null = null;
  private intervalMs = AUDIT_ANCHOR_INTERVAL_MS;
  private lastPassAt: number | null = null;

  private readonly chain: ChainReader;
  private readonly sink: AuditAnchorSink;
  private readonly lease: LeaderLease;
  private readonly holderId: string;
  private readonly logger: HarnessLogger;
  private readonly now: () => number;

  constructor(options: AuditAnchorServiceOptions) {
    this.chain = options.chain;
    this.sink = options.sink;
    this.lease = options.lease;
    this.holderId = options.holderId;
    this.logger = options.logger ?? silentHarnessLogger;
    this.now = options.now ?? Date.now;
  }

  start(intervalMs = AUDIT_ANCHOR_INTERVAL_MS): void {
    if (this.timer) return;
    this.intervalMs = intervalMs;
    this.timer = detachedInterval(
      () => {
        if (this.beating) return;
        this.beating = this.beat().finally(() => {
          this.beating = null;
        });
      },
      Math.min(AUDIT_ANCHOR_RENEW_MS, intervalMs),
    );
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    // A renewal still in flight would take the lease back after the release below.
    if (this.beating) await this.beating;
    while (this.running) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    try {
      await this.lease.release(AUDIT_ANCHOR_LEASE_NAME, this.holderId);
    } catch (error) {
      this.logger.warn(
        'audit.anchor_lease_release_failed',
        harnessLogMeta('audit.anchor_lease_release_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /** Renews the lease; anchors when leadership was just taken or an interval has passed. */
  async beat(): Promise<number> {
    if (!(await this.renew())) {
      this.lastPassAt = null;
      return 0;
    }
    const now = this.now();
    if (this.lastPassAt !== null && now - this.lastPassAt < this.intervalMs) {
      return 0;
    }
    this.lastPassAt = now;
    return this.anchorPass();
  }

  /** Anchors up to one batch of heads, if this replica holds the lease; returns how many. */
  async tick(): Promise<number> {
    if (this.running) return 0;
    if (!(await this.renew())) return 0;
    return this.anchorPass();
  }

  private async renew(): Promise<boolean> {
    try {
      return await this.lease.tryAcquire(
        AUDIT_ANCHOR_LEASE_NAME,
        this.holderId,
        AUDIT_ANCHOR_LEASE_MS,
      );
    } catch (error) {
      this.logger.error(
        'audit.anchor_tick_failed',
        harnessLogMeta('audit.anchor_tick_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return false;
    }
  }

  private async anchorPass(): Promise<number> {
    if (this.running) return 0;
    this.running = true;
    let anchored = 0;
    try {
      const heads = await this.chain.unanchored(AUDIT_ANCHOR_BATCH);
      const stale = new Map(
        (await this.chain.unlinked())
          .filter(
            (unlinked) =>
              unlinkedAgeMs(unlinked, this.now()) >= AUDIT_LINK_STALE_MS,
          )
          .map((unlinked) => [unlinked.orgId, unlinked]),
      );
      for (const head of heads) {
        const behind = stale.get(head.orgId);
        if (behind !== undefined) {
          // An anchor says the chain up to it is whole: not while entries committed long ago are not on it.
          this.logger.error(
            'audit.anchor_refused',
            harnessLogMeta('audit.anchor_refused', {
              orgId: head.orgId,
              seq: head.seq,
              reason: 'unlinked_entries',
              appends: behind.appends,
              oldestAgeS: Math.floor(unlinkedAgeMs(behind, this.now()) / 1000),
            }),
          );
          continue;
        }
        try {
          await forEnumeratedOrg(
            head.orgId,
            'agent-harness.audit-anchor',
            async () => {
              await this.sink.write({
                orgId: head.orgId,
                seq: head.seq,
                hash: head.hash,
                anchoredAt: new Date(this.now()).toISOString(),
              });
              await this.chain.markAnchored(head.orgId, head.seq);
            },
          );
          anchored += 1;
          this.logger.info(
            'audit.anchored',
            harnessLogMeta('audit.anchored', {
              orgId: head.orgId,
              seq: head.seq,
              hash: head.hash,
              sink: this.sink.name,
            }),
          );
        } catch (error) {
          this.logger.error(
            'audit.anchor_failed',
            harnessLogMeta('audit.anchor_failed', {
              orgId: head.orgId,
              seq: head.seq,
              sink: this.sink.name,
              errorType: error instanceof Error ? error.name : typeof error,
            }),
          );
        }
      }
      return anchored;
    } catch (error) {
      this.logger.error(
        'audit.anchor_tick_failed',
        harnessLogMeta('audit.anchor_tick_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return anchored;
    } finally {
      this.running = false;
    }
  }
}

/** Reads an org's whole chain, its head and its anchors, and checks them. */
export class AuditVerifier {
  constructor(
    private readonly chain: Pick<ChainReader, 'read' | 'head' | 'unlinked'>,
    private readonly sink: AuditAnchorSink,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  async verify(orgId: string): Promise<AuditVerification> {
    const records: AuditRecord[] = [];
    let after = 0;
    for (;;) {
      const page = await this.chain.read(orgId, after, VERIFY_PAGE);
      records.push(...page);
      if (page.length < VERIFY_PAGE) break;
      after = page[page.length - 1]?.seq ?? after;
    }
    const verification = verifyAuditChain(records, {
      head: await this.chain.head(orgId),
      anchors: await this.sink.list(orgId),
      unlinked: (await this.chain.unlinked(orgId))[0]?.appends ?? 0,
    });
    if (verification.ok) {
      this.logger.info(
        'audit.verified',
        harnessLogMeta('audit.verified', {
          orgId,
          records: verification.records,
          seq: verification.lastSeq,
          unlinked: verification.unlinked,
        }),
      );
    } else {
      this.logger.error(
        'audit.verify_failed',
        harnessLogMeta('audit.verify_failed', {
          orgId,
          records: verification.records,
          seq: verification.lastSeq,
          unlinked: verification.unlinked,
          problems: verification.problems.map(
            (problem) => `${problem.kind}@${String(problem.seq)}`,
          ),
        }),
      );
    }
    return verification;
  }
}
