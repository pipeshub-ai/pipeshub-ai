import type { OrgDataKeys } from '../../../../modules/agent-harness/application/org-data-keys';
import type {
  OrgDataStore,
  PrincipalErasureJobStore,
} from '../../../../modules/agent-harness/ports/principal-erasure';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoOrgData, MongoPrincipalErasureJobs } from './mongo';
import { PgOrgData, PgPrincipalErasureJobs } from './pg';

export const createPrincipalErasureJobs = (): PrincipalErasureJobStore<Tx> =>
  pickHarnessStore<PrincipalErasureJobStore<Tx>>(
    'agent-harness/principal-erasure',
    {
      mongo: () => new MongoPrincipalErasureJobs(),
      postgres: () => new PgPrincipalErasureJobs(),
    },
  );

/** The org's data its erasure removes; `keys`, the key ring whose cache the shred drops too. */
export const createOrgData = (
  keys?: Pick<OrgDataKeys, 'shred'>,
): OrgDataStore<Tx> =>
  pickHarnessStore<OrgDataStore<Tx>>('agent-harness/principal-erasure', {
    mongo: () => new MongoOrgData(keys),
    postgres: () => new PgOrgData(keys),
  });
