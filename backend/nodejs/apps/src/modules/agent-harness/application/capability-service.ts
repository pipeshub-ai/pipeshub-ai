import {
  CAPABILITY_REFRESH_MS,
  CAPABILITY_RETRY_MIN_MS,
  CAPABILITY_SETTLE_WAIT_MS,
  CapabilityKey,
  capabilityKnown,
  capabilitiesSettled,
  CapabilitySnapshot,
  capabilitySnapshot,
  DeploymentCapabilities,
  LEAST_CAPABILITIES,
} from '../domain/capabilities';
import { CapabilityProbe, CapabilitySource } from '../ports/capability-probe';
import { harnessLogMeta } from './harness-log';
import { detachedInterval } from '../../../libs/context/orgContext';

export interface CapabilityLogger {
  info(message: string, metadata?: Record<string, unknown>): void;
  warn(message: string, metadata?: Record<string, unknown>): void;
}

/**
 * Derives the deployment's capabilities from probes (no configuration knob
 * decides them) and keeps the latest set for claims to pin (REPORT 11). A
 * round in which a probe could not answer leaves what it provides unknown
 * and is retried on a short backoff (ADR-CAP-01).
 */
export class HarnessCapabilityService implements CapabilitySource {
  private snapshot: CapabilitySnapshot | null = null;
  private timer: NodeJS.Timeout | null = null;
  private retryTimer: NodeJS.Timeout | null = null;
  private retryDelayMs = CAPABILITY_RETRY_MIN_MS;
  private intervalMs = CAPABILITY_REFRESH_MS;
  private refreshing: Promise<CapabilitySnapshot> | null = null;

  constructor(
    private readonly probes: readonly CapabilityProbe[],
    private readonly logger?: CapabilityLogger,
    private readonly now: () => number = Date.now,
  ) {}

  current(): CapabilitySnapshot | null {
    return this.snapshot;
  }

  /** Every capability proved either way (readiness): false before the first round and while a probe cannot answer. */
  settled(): boolean {
    return capabilitiesSettled(this.snapshot);
  }

  /** Concurrent callers share one probe round. */
  refresh(): Promise<CapabilitySnapshot> {
    this.refreshing ??= this.probeAll().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  start(intervalMs = CAPABILITY_REFRESH_MS): void {
    if (this.timer) return;
    this.intervalMs = intervalMs;
    this.timer = detachedInterval(() => {
      void this.refresh();
    }, intervalMs);
    this.scheduleRetry();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.cancelRetry();
  }

  /** While started, an unsettled round is asked again soon, doubling up to the period; a settled one resets it. */
  private scheduleRetry(): void {
    if (this.timer === null) return;
    if (this.snapshot === null || capabilitiesSettled(this.snapshot)) {
      this.cancelRetry();
      this.retryDelayMs = CAPABILITY_RETRY_MIN_MS;
      return;
    }
    if (this.retryTimer !== null) return;
    const delay = Math.min(this.retryDelayMs, this.intervalMs);
    this.retryDelayMs = Math.min(this.retryDelayMs * 2, this.intervalMs);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.refresh();
    }, delay);
    this.retryTimer.unref();
  }

  private cancelRetry(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private async probeAll(): Promise<CapabilitySnapshot> {
    const unsettled: CapabilityKey[] = [];
    const results = await Promise.all(
      this.probes.map(async (probe) => {
        try {
          return await probe.probe();
        } catch (error) {
          unsettled.push(...probe.provides);
          this.logger?.warn(
            'capability.probe_failed',
            harnessLogMeta('capability.probe_failed', {
              probe: probe.name,
              errorType: error instanceof Error ? error.name : typeof error,
              outcome: 'unknown',
              retryInMs: Math.min(this.retryDelayMs, this.intervalMs),
            }),
          );
          return {};
        }
      }),
    );
    const capabilities: DeploymentCapabilities = { ...LEAST_CAPABILITIES };
    for (const result of results) Object.assign(capabilities, result);
    // A probe that answered proves its value even where another, failing, also provides it.
    for (const result of results) {
      for (const key of Object.keys(result) as CapabilityKey[]) {
        const at = unsettled.indexOf(key);
        if (at >= 0) unsettled.splice(at, 1);
      }
    }
    const next = capabilitySnapshot(capabilities, this.now(), unsettled);
    const previous = this.snapshot;
    if (previous?.digest !== next.digest) {
      const changed = (
        Object.keys(next.capabilities) as Array<keyof DeploymentCapabilities>
      ).filter((key) => previous?.capabilities[key] !== next.capabilities[key]);
      this.logger?.info(
        'capability.changed',
        harnessLogMeta('capability.changed', {
          changed: changed.map(
            (key) => `${key}=${String(next.capabilities[key])}`,
          ),
          digest: next.digest,
          previousDigest: previous?.digest ?? null,
        }),
      );
    }
    // The first round's state is in its own lines (probe_failed, changed); only a later turn of it is news.
    if (
      previous !== null &&
      capabilitiesSettled(previous) !== capabilitiesSettled(next)
    ) {
      this.logger?.info(
        'capability.settled',
        harnessLogMeta('capability.settled', {
          settled: capabilitiesSettled(next),
          unknown: [...(next.unsettled ?? [])],
        }),
      );
    }
    this.snapshot = next;
    this.scheduleRetry();
    return next;
  }
}

/**
 * The snapshot once `key` is proved either way, waiting for at most one more
 * probe round (bounded by `waitMs`) when it is unknown: what admission and a
 * claim read, so a round that timed out is never taken for an answer.
 */
export async function settledCapabilities(
  source: CapabilitySource,
  key: CapabilityKey,
  waitMs: number = CAPABILITY_SETTLE_WAIT_MS,
): Promise<CapabilitySnapshot | null> {
  const before = source.current();
  if (capabilityKnown(before, key) || source.refresh === undefined) {
    return before;
  }
  let timer: NodeJS.Timeout | undefined;
  await Promise.race([
    source.refresh().catch(() => undefined),
    new Promise<void>((resolve) => {
      timer = setTimeout(resolve, waitMs);
      timer.unref();
    }),
  ]);
  clearTimeout(timer);
  return source.current();
}
