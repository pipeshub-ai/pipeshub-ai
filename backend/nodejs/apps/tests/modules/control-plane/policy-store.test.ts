/**
 * CPD-4: the policy store's purge contract in memory, the migration ledger's
 * and the legacy policy source's. The `PolicyStore` contract runs in
 * skeleton-stores.test.ts and over Mongo in policy-store.mongo.itest.ts.
 */
import { InMemoryPolicyStore } from './support/in-memory-stores';
import { InMemoryLegacyPolicySource } from './support/legacy-policy-source';
import { describeLegacyPolicySourceContract } from './support/legacy-policy-source-contract';
import { InMemoryMigrationLedger } from './support/in-memory-ledger';
import { describeMigrationLedgerContract } from './support/migration-ledger-contract';
import { describePolicyStorePurgeContract } from './support/policy-store-contract';
import { auditedControlPlane } from './support/in-memory';

describePolicyStorePurgeContract('InMemoryPolicyStore', async () => ({ store: new InMemoryPolicyStore() }));
describeMigrationLedgerContract('InMemoryMigrationLedger', async () => ({
  ledger: new InMemoryMigrationLedger(),
  transaction: auditedControlPlane().transaction,
}));
describeLegacyPolicySourceContract('InMemoryLegacyPolicySource', async () => {
  const source = new InMemoryLegacyPolicySource()
    .set('org-1', 'org:org-1', { schemaVersion: 1, scope: { kind: 'org', id: 'org-1' }, memory: { mode: 'user', review: 'auto' } })
    .set('org-1', 'agent:agent-b', {
      schemaVersion: 1,
      scope: { kind: 'agent', id: 'agent-b' },
      approvals: { writes: 'ask' },
      lockedBy: { approvals: 'org' },
    });
  return { source, down: () => (source.failAfter = 0) };
});

