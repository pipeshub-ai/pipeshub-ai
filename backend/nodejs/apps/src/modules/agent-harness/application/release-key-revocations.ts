import { detachedInterval } from '../../../libs/context/orgContext';
import { HarnessLogger, silentHarnessLogger } from './harness-log';
import {
  applyRevocations,
  formatInstant,
  RevocationRefused,
  revocationsInForce,
} from '../domain/release-keys';

/**
 * R41SUPPLY-FU-6 (ADR-SEC-06 D1): Node reads the deployment's last good
 * revocation list, which the harness worker fetches and stores, at start and
 * every few minutes, and checks its signature itself (the store is trusted
 * for availability only). A store that is down, or a copy that is refused,
 * leaves the list in force; only a revoked or expired key fails closed.
 */
export const REVOCATIONS_CONFIG_KEY = '/services/harness/revokedReleaseKeys';
export const REVOCATIONS_READ_MS = 10 * 60_000;

export interface StoredRevocations {
  get<T>(key: string): Promise<T | null>;
}

export class ReleaseKeyRevocationReader {
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly store: StoredRevocations,
    private readonly logger: HarnessLogger = silentHarnessLogger,
    private readonly intervalMs: number = REVOCATIONS_READ_MS,
  ) {}

  /** Never throws: whatever fails, the list in force stays. */
  async readOnce(now: number = Date.now()): Promise<void> {
    let stored: unknown;
    try {
      stored = await this.store.get<unknown>(REVOCATIONS_CONFIG_KEY);
    } catch (error) {
      this.logger.warn('agent_harness.release_keys.store_unreadable', {
        errorType: error instanceof Error ? error.name : typeof error,
      });
      return;
    }
    if (typeof stored !== 'object' || stored === null) return;
    const { document, signature } = stored as Record<string, unknown>;
    if (typeof document !== 'string' || typeof signature !== 'string') return;
    const before = revocationsInForce();
    try {
      const after = applyRevocations(document, signature, now);
      if (after !== before) {
        this.logger.warn('agent_harness.release_keys.revocations_applied', {
          issued: after.issued === null ? null : formatInstant(after.issued),
          revoked: [...after.revoked].sort(),
        });
      }
    } catch (error) {
      if (!(error instanceof RevocationRefused)) throw error;
      if (error.reason !== 'stale') {
        this.logger.warn('agent_harness.release_keys.revocations_refused', {
          reason: error.reason,
        });
      }
    }
  }

  start(): void {
    if (this.timer !== null) return;
    void this.readOnce();
    this.timer = detachedInterval(() => void this.readOnce(), this.intervalMs);
  }

  stop(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }
}
