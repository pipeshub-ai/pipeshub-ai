/** CPD-7: the capability migration and its dual-write sync (MG, DW) and the ledger contract (LG) on the in-memory stores. */
import { InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';
import { describeCapabilityLedgerContract } from './support/capability-ledger-contract';
import { describeCapabilityMigrationCases } from './support/capability-migration-cases';
import type { CapabilityWorld } from './support/capability-world';
import { auditedControlPlane } from './support/in-memory';

const inMemoryWorld = async (): Promise<CapabilityWorld> => {
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const controlPlane = auditedControlPlane();
  return {
    installations,
    grants,
    ledger: new InMemoryCapabilityLedger(installations, grants),
    transaction: controlPlane.transaction,
    configEntries: async (orgId) => controlPlane.configEntries(orgId).length,
  };
};

describeCapabilityLedgerContract('InMemoryCapabilityLedger', inMemoryWorld);
describeCapabilityMigrationCases('in-memory stores', inMemoryWorld);
