/** G2 (ADR-INBOX-01): the InboxStore contract on the in-memory store (Mongo and PostgreSQL run it in their itests). */
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { describeInboxStoreContract } from './support/inbox-store-contract';

describeInboxStoreContract('in memory', async () => {
  const store = new InMemoryInboxStore();
  return { store, replica: () => store, orgs: ['org-a', 'org-b'], users: ['user-1', 'user-2'] };
});
