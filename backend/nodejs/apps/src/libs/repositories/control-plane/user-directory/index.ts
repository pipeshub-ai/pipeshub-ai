import type {
  GroupMembership,
  OrgRoles,
} from '../../../../modules/control-plane/ports/org-roles';
import { pickHarnessStore } from '../../agent-harness/stores';
import { LiveOrgRoles, UserGroupMembership } from './mongo';
import { RepositoryGroupMembership } from './pg';

/** The live org role reads main's user repository on either store. */
export const createOrgRoles = (): OrgRoles => new LiveOrgRoles();

export const createGroupMembership = (): GroupMembership =>
  pickHarnessStore<GroupMembership>('control-plane/user-directory', {
    mongo: () => new UserGroupMembership(),
    postgres: () => new RepositoryGroupMembership(),
  });
