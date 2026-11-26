import type { Db } from 'mongodb';
import {
  JournalCapability,
  recordJournalCapability,
} from '../../../../modules/agent-harness/config';
import { DeploymentCapabilities } from '../../../../modules/agent-harness/domain/capabilities';
import { CapabilityProbe } from '../../../../modules/agent-harness/ports/capability-probe';
import { probeJournalDurability } from '../journal-startup/mongo';

/**
 * `journal.durable` from S1's replica-set / majority-write probe. It also
 * records the result where admission readiness reads it, so one probe decides
 * both whether the harness admits and what a claim pins.
 */
export class JournalDurabilityProbe implements CapabilityProbe {
  readonly name = 'journal';
  readonly provides = ['journal.durable'] as const;

  constructor(
    private readonly probeJournal: (
      db?: Db,
    ) => Promise<JournalCapability> = probeJournalDurability,
    private readonly record: (
      capability: JournalCapability,
    ) => void = recordJournalCapability,
  ) {}

  async probe(): Promise<Partial<DeploymentCapabilities>> {
    let journal: JournalCapability;
    try {
      journal = await this.probeJournal();
    } catch {
      journal = { durable: false, reason: 'probe_failed' };
    }
    this.record(journal);
    return { 'journal.durable': journal.durable };
  }
}
