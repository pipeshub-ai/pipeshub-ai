import { oidOf } from '../../pg-util';
import { getUserRepository } from '../../user';
import { inBoundOrgUnit } from '../pg-documents/ambient';
import type { ApproverPeople } from './people';

/** `ApproverPeople` on PostgreSQL, through main's user repository (`identity.users`). */
export class PgApproverPeople implements ApproverPeople {
  async isLiveUser(orgId: string, userId: string): Promise<boolean> {
    if (oidOf(orgId) === undefined || oidOf(userId) === undefined) return false;
    return (
      (await inBoundOrgUnit(orgId, () =>
        getUserRepository().getLive(orgId, userId),
      )) !== null
    );
  }
}
