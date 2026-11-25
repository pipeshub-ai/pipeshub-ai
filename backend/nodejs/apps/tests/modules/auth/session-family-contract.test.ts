import { describeSessionFamilyContract } from '../../stores/contract/session-family.contract';
import { InMemorySessionFamilies } from '../../support/auth/in-memory-session-families';

// The in-memory store the unit tests use answers as MongoDB and PostgreSQL do (mongo-identity, pg-identity itests).
describe('session-family store contract (in memory)', () => {
  let n = 0;
  describeSessionFamilyContract({
    repo: new InMemorySessionFamilies(),
    newOrg: () => `org-${++n}`,
    newId: () => `id-${++n}`,
  });
});
