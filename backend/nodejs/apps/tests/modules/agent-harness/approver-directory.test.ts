import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import {
  ApproverOrgSeed,
  describeApproverDirectoryContract,
} from './support/approver-directory-contract';

const byOrg = (orgs: ApproverOrgSeed[], ids: (org: ApproverOrgSeed) => string[]) =>
  new Map(orgs.map((org) => [org.orgId, new Set(ids(org))]));

const liveUsers = (org: ApproverOrgSeed) => (org.users ?? []).filter((user) => !user.deleted);

// Only the run's actor approves here, so the in-memory directory holds no approvers.
describeApproverDirectoryContract('InMemoryApproverDirectory', async (orgs) =>
  new InMemoryApproverDirectory(
    new Map(),
    byOrg(orgs, (org) => liveUsers(org).filter((user) => user.admin).map((user) => user.userId)),
  ),
);
