import type { CapabilityRepair, OrgReport } from '../../control-plane';
import { type HarnessLogger, silentHarnessLogger } from './harness-log';

/** Long enough for the query service, which serves the legacy facts, to come up beside a restarted Node. */
export const CAPABILITY_REPAIR_START_DELAY_MS = 30_000;
/** How long an org whose repair failed waits before one of its turns asks again. */
export const CAPABILITY_REPAIR_RETRY_MS = 60_000;

export interface OrgCapabilityRepairDeps {
  readonly migration: CapabilityRepair;
  /** Every org of the deployment, for the start-up pass. */
  readonly orgs: () => Promise<string[]>;
  /** The org predicate (ADR-OPS-08). */
  readonly harnessOn: (orgId: string) => Promise<boolean>;
  readonly logger?: HarnessLogger;
  readonly now?: () => number;
  readonly retryAfterMs?: number;
}

/**
 * Catches up an org's control-plane grants and installations when its harness turns on (ADR-OPS-08). While the
 * harness is off for an org, the query service's CPD-7 dual write sends nothing, so an org recorded as migrated holds
 * stale grants for whatever changed meanwhile. Both edges where the harness turns on run the capability migration's
 * repair for the org: a Labs save from off to on (`request`), and a process starting with the switch on for every org
 * whose harness is on (`start`). Each runs in the background and never fails its caller; a failed repair is logged,
 * and the org's next turn (`noteTurn`) or the next start tries again. The repair is idempotent, so running it on an
 * org already in line writes nothing.
 */
export class OrgCapabilityRepair {
  /** Orgs whose last repair failed, with when a turn may ask again. */
  private readonly owed = new Map<string, number>();
  private readonly running = new Map<string, Promise<boolean>>();
  private readonly logger: HarnessLogger;
  private readonly now: () => number;
  private readonly retryAfterMs: number;
  private timer: NodeJS.Timeout | null = null;
  private starting: Promise<void> | null = null;

  constructor(private readonly deps: OrgCapabilityRepairDeps) {
    this.logger = deps.logger ?? silentHarnessLogger;
    this.now = deps.now ?? Date.now;
    this.retryAfterMs = deps.retryAfterMs ?? CAPABILITY_REPAIR_RETRY_MS;
  }

  /** Repairs the org now, or joins the repair under way; true when it succeeded. Never rejects. */
  request(orgId: string): Promise<boolean> {
    const current = this.running.get(orgId);
    if (current !== undefined) return current;
    const run = this.repairOnce(orgId).finally(() => {
      this.running.delete(orgId);
    });
    this.running.set(orgId, run);
    return run;
  }

  /** A harness turn of the org: one whose last repair failed tries again, at most once per retry interval. */
  noteTurn(orgId: string): void {
    const due = this.owed.get(orgId);
    if (due === undefined || this.running.has(orgId) || this.now() < due) {
      return;
    }
    void this.request(orgId);
  }

  /** Schedules the start-up pass; the timer never holds the process open. */
  start(delayMs: number = CAPABILITY_REPAIR_START_DELAY_MS): void {
    if (this.timer !== null || this.starting !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.starting = this.repairOrgsOn();
    }, delayMs);
    this.timer.unref();
  }

  /** Cancels a pass not yet begun and waits for one under way. */
  async stop(): Promise<void> {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    await this.starting;
    await Promise.all(this.running.values());
  }

  /** The start-up pass: every org whose harness is on, one at a time. */
  async repairOrgsOn(): Promise<void> {
    let orgs: string[];
    try {
      orgs = await this.deps.orgs();
    } catch (error) {
      this.logger.warn('control_plane.capability_repair_failed', {
        reasonCode: 'orgs_unavailable',
        errorType: errorType(error),
      });
      return;
    }
    for (const orgId of orgs) {
      if (await this.deps.harnessOn(orgId).catch(() => false)) {
        await this.request(orgId);
      }
    }
  }

  private async repairOnce(orgId: string): Promise<boolean> {
    let failed: string | null;
    let report: OrgReport | null = null;
    try {
      report = await this.deps.migration.repair(orgId);
      failed = report?.failed ?? null;
    } catch (error) {
      failed = reasonOf(error);
    }
    if (failed === null) {
      this.owed.delete(orgId);
      if (
        report !== null &&
        report.created + report.updated + report.removed > 0
      ) {
        this.logger.info('control_plane.capability_repaired', {
          orgId,
          created: report.created,
          updated: report.updated,
          removed: report.removed,
        });
      }
      return true;
    }
    this.owed.set(orgId, this.now() + this.retryAfterMs);
    this.logger.warn('control_plane.capability_repair_failed', {
      orgId,
      reasonCode: failed,
    });
    return false;
  }
}

function reasonOf(error: unknown): string {
  const reason = (error as { reason?: unknown } | null)?.reason;
  return typeof reason === 'string' ? reason : 'capability_repair_failed';
}

const errorType = (error: unknown): string =>
  error instanceof Error ? error.name : typeof error;
