import {
  JournalCapability,
  recordJournalCapability,
} from '../../../../modules/agent-harness/config';
import { DeploymentCapabilities } from '../../../../modules/agent-harness/domain/capabilities';
import { CapabilityProbe } from '../../../../modules/agent-harness/ports/capability-probe';
import { harnessStoresWithoutPostgres } from '../stores';

/**
 * `journal.durable` on PostgreSQL: a commit is durable once acknowledged, so the answer is whether every harness store
 * is on PostgreSQL. A store left on MongoDB keeps the harness off (`postgres_incomplete`), never half in each database.
 */
export class PgJournalDurabilityProbe implements CapabilityProbe {
  readonly name = 'journal';
  readonly provides = ['journal.durable'] as const;

  constructor(
    private readonly missing: () => string[] = harnessStoresWithoutPostgres,
    private readonly record: (
      capability: JournalCapability,
    ) => void = recordJournalCapability,
  ) {}

  probe(): Promise<Partial<DeploymentCapabilities>> {
    const journal: JournalCapability =
      this.missing().length === 0
        ? { durable: true, reason: 'postgres' }
        : { durable: false, reason: 'postgres_incomplete' };
    this.record(journal);
    return Promise.resolve({ 'journal.durable': journal.durable });
  }
}
