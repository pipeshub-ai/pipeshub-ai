/** The AuditChainStore and AuditChainQueries contracts on `InMemoryAuditChainStore`; Mongo runs them in audit-chain-store.mongo.itest.ts. */
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { AuditChainBackend, describeAuditChainStoreContract } from './support/audit-chain-store-contract';
import { describeAuditChainQueriesContract } from './support/audit-chain-queries-contract';

const inMemory = async (): Promise<AuditChainBackend<undefined>> => ({
  store: new InMemoryAuditChainStore(),
  transact: (work) => work(undefined),
  atomic: false,
});

describeAuditChainStoreContract('in-memory', inMemory);
describeAuditChainQueriesContract('in-memory', inMemory);
