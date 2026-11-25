/**
 * In-memory facts behind the CPD-9 services: live org roles, team
 * memberships, agents, the org's delegation and named approvers.
 */
import type { OrgRole, OrgRoles, DelegationSource } from '../../../../src/modules/control-plane/ports/org-roles';
import { InMemoryAgentDirectory, InMemoryMembership } from './in-memory-services';

export class InMemoryOrgRoles implements OrgRoles {
  readonly roles = new Map<string, OrgRole>();
  reads = 0;

  set(orgId: string, userId: string, role: OrgRole | null): void {
    if (role === null) this.roles.delete(`${orgId}|${userId}`);
    else this.roles.set(`${orgId}|${userId}`, role);
  }

  async roleOf(orgId: string, userId: string): Promise<OrgRole | null> {
    this.reads += 1;
    return this.roles.get(`${orgId}|${userId}`) ?? null;
  }
}

export class InMemoryDelegation implements DelegationSource {
  readonly actions = new Map<string, readonly string[]>();

  async delegatedActions(orgId: string): Promise<readonly string[]> {
    return this.actions.get(orgId) ?? [];
  }
}

export interface DirectoryFacts {
  readonly roles: InMemoryOrgRoles;
  readonly membership: InMemoryMembership;
  readonly agents: InMemoryAgentDirectory;
  readonly delegation: InMemoryDelegation;
}

export const directoryFacts = (): DirectoryFacts => ({
  roles: new InMemoryOrgRoles(),
  membership: new InMemoryMembership(),
  agents: new InMemoryAgentDirectory(),
  delegation: new InMemoryDelegation(),
});
