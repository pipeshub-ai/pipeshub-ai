import {
  AUDIT_LINK_STALE_MS,
  AUDIT_LINK_WATCH_MS,
  AuditUnlinked,
  unlinkedAgeMs,
} from '../domain/audit';
import { AuditChainStore } from '../ports/audit';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import {
  detachedInterval,
  forEnumeratedOrg,
} from '../../../libs/context/orgContext';

/** What an operator watches: an oldest age that keeps growing is a link that does not run. */
export interface AuditLinkMetrics {
  unlinked(appends: number, oldestAgeS: number): void;
}

export interface AuditLinkWatchOptions {
  chain: Pick<AuditChainStore<unknown>, 'head' | 'unlinked'>;
  metrics: AuditLinkMetrics;
  logger?: HarnessLogger;
  now?: () => number;
}

/**
 * Finds audit appends whose link did not run (the process that committed
 * them died first), links them, and reports what stays unlinked. Every Node
 * runs it: after a restart the entries are on the chain within one interval,
 * with nothing for an operator to do.
 */
export class AuditLinkWatch {
  private timer: NodeJS.Timeout | null = null;
  private running: Promise<AuditUnlinked[]> | null = null;

  private readonly chain: AuditLinkWatchOptions['chain'];
  private readonly metrics: AuditLinkMetrics;
  private readonly logger: HarnessLogger;
  private readonly now: () => number;

  constructor(options: AuditLinkWatchOptions) {
    this.chain = options.chain;
    this.metrics = options.metrics;
    this.logger = options.logger ?? silentHarnessLogger;
    this.now = options.now ?? Date.now;
  }

  start(intervalMs = AUDIT_LINK_WATCH_MS): void {
    if (this.timer) return;
    this.timer = detachedInterval(() => {
      void this.tick();
    }, intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    await this.running;
  }

  /** Links what it finds; returns what is still unlinked afterwards. */
  tick(): Promise<AuditUnlinked[]> {
    this.running ??= this.look().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async look(): Promise<AuditUnlinked[]> {
    try {
      // A read links first: reading each org's head is asking for its link.
      for (const found of await this.chain.unlinked()) {
        await forEnumeratedOrg(found.orgId, 'agent-harness.audit-link', () =>
          this.chain.head(found.orgId),
        );
      }
      const left = await this.chain.unlinked();
      const now = this.now();
      const ages = left.map((unlinked) => unlinkedAgeMs(unlinked, now));
      this.metrics.unlinked(
        left.reduce((sum, unlinked) => sum + unlinked.appends, 0),
        Math.max(0, ...ages) / 1000,
      );
      left.forEach((unlinked, index) => {
        if ((ages[index] ?? 0) < AUDIT_LINK_STALE_MS) return;
        this.logger.error(
          'audit.link_stale',
          harnessLogMeta('audit.link_stale', {
            orgId: unlinked.orgId,
            appends: unlinked.appends,
            oldestAgeS: Math.floor((ages[index] ?? 0) / 1000),
            choice: 'anchor_held_back',
          }),
        );
      });
      return left;
    } catch (error) {
      this.logger.error(
        'audit.link_watch_failed',
        harnessLogMeta('audit.link_watch_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return [];
    }
  }
}
