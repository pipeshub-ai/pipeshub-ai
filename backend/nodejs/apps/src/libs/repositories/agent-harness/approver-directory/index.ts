import { pickHarnessStore } from '../stores';
import { MongoApproverPeople } from './mongo';
import type { ApproverPeople } from './people';
import { PgApproverPeople } from './pg';

export type { ApproverPeople };

/** The org's users and groups as the approver directory reads them, on the selected document store. */
export const createApproverPeople = (): ApproverPeople =>
  pickHarnessStore<ApproverPeople>('agent-harness/approver-directory', {
    mongo: () => new MongoApproverPeople(),
    postgres: () => new PgApproverPeople(),
  });
