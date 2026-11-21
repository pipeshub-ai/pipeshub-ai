import type { HeadRevisions } from '../../../control-plane';
import type { HarnessRolloutSettings } from '../../domain/rollout';
import type { HarnessRolloutSettingsSource } from '../../ports/harness-rollout';

interface Held {
  head: number;
  at: number;
  settings: HarnessRolloutSettings;
}

/**
 * The org's Labs switches read once per org head (CPD-5, R2 F9): a Labs save
 * is a control-plane write and moves the head, so the next admission reads
 * the KV document again and every other admission reads only the head.
 * `ttlMs` bounds what an edit made outside the control plane (an operator in
 * the KV store) can wait. An unreadable head reads the document, as before.
 */
export class HeadCachedRolloutSettings implements HarnessRolloutSettingsSource {
  private readonly held = new Map<string, Held>();

  constructor(
    private readonly inner: HarnessRolloutSettingsSource,
    private readonly heads: HeadRevisions,
    private readonly ttlMs = 30_000,
    private readonly now: () => number = Date.now,
  ) {}

  async read(orgId: string): Promise<HarnessRolloutSettings> {
    let head: number;
    try {
      head = await this.heads.head(orgId);
    } catch {
      return this.inner.read(orgId);
    }
    const held = this.held.get(orgId);
    if (held?.head === head && this.now() - held.at < this.ttlMs) {
      return held.settings;
    }
    const settings = await this.inner.read(orgId);
    this.held.set(orgId, { head, at: this.now(), settings });
    return settings;
  }
}
