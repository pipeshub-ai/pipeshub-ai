/** P7.3 AD, SV-8, ER on the in-memory stores (the Mongo half is memory-store.mongo.itest.ts). */
import { InMemoryMemoryStore } from '../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { inMemoryCollaborators } from './support/in-memory-collaborators';
import { describeMemoryAdmissionContract } from './support/memory-admission-contract';

describeMemoryAdmissionContract(
  'in-memory',
  inMemoryCollaborators,
  async () => new InMemoryMemoryStore(),
);
