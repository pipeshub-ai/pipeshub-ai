/**
 * CPD-9 AZ-1..3, AZV-1..4 (CPA-16): the production `RoleAuthorizer` on
 * CP-SKEL's contract and the shared matrix `contracts/control-plane/vectors/authz.json`.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'chai';
import { RoleAuthorizer } from '../../../src/modules/control-plane/application/role-authorizer';
import type { CpAction, ResourceRef } from '../../../src/modules/control-plane/ports/authorizer';
import type { OrgRole } from '../../../src/modules/control-plane/ports/org-roles';
import type { TeamMembership } from '../../../src/modules/control-plane/domain/resolution';
import { describeAuthorizerContract } from './support/authorizer-contract';
import { directoryFacts, type DirectoryFacts } from './support/directory-world';

interface AuthzVector {
  id: string;
  behaviour: string;
  actor: string;
  action: CpAction;
  resource: ResourceRef;
  delegation: string[];
  expect: 'allow' | 'deny';
  reason?: string;
}
interface AuthzVectors {
  orgId: string;
  world: {
    roles: Record<string, OrgRole>;
    teams: Record<string, TeamMembership[]>;
    agents: Record<string, { editors: string[]; ownerTeam: string | null }>;
  };
  cases: AuthzVector[];
}

const VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/control-plane/vectors/authz.json'), 'utf8'),
) as AuthzVectors;

const authorizerOver = (facts: DirectoryFacts): RoleAuthorizer =>
  new RoleAuthorizer(facts.roles, facts.membership, facts.agents, facts.delegation);

function vectorWorld(): DirectoryFacts {
  const facts = directoryFacts();
  const org = VECTORS.orgId;
  for (const [user, role] of Object.entries(VECTORS.world.roles)) facts.roles.set(org, user, role);
  for (const [user, teams] of Object.entries(VECTORS.world.teams)) facts.membership.teams.set(`${org}|${user}`, teams);
  for (const [key, agent] of Object.entries(VECTORS.world.agents)) {
    facts.agents.agents.set(`${org}|${key}`, { definitionVersion: 1, runners: [], editors: agent.editors, ownerTeam: agent.ownerTeam });
  }
  return facts;
}

describeAuthorizerContract('RoleAuthorizer', async () => {
  const facts = directoryFacts();
  return {
    authorizer: authorizerOver(facts),
    setRole: async (orgId, userId, role) => facts.roles.set(orgId, userId, role),
  };
});

describe('CPD-9 RoleAuthorizer: the authz matrix (CPA-16)', () => {
  for (const vector of VECTORS.cases) {
    it(`${vector.id}: ${vector.behaviour}`, async () => {
      const facts = vectorWorld();
      facts.delegation.actions.set(VECTORS.orgId, vector.delegation);
      const decision = await authorizerOver(facts).check(
        { orgId: VECTORS.orgId, userId: vector.actor },
        vector.action,
        vector.resource,
      );
      expect(decision.allow ? 'allow' : 'deny').to.equal(vector.expect);
      if (!decision.allow) expect(decision.reason).to.equal(vector.reason);
    });
  }

  it('AZV-3: membership unreadable denies the team rule only; the org admin is unaffected', async () => {
    const facts = vectorWorld();
    facts.delegation.actions.set(VECTORS.orgId, ['installation.create']);
    facts.membership.available = false;
    const authorizer = authorizerOver(facts);
    const teamObject = { orgId: VECTORS.orgId, type: 'installation', owner: 'team:t9' };
    expect(await authorizer.check({ orgId: VECTORS.orgId, userId: 'u-team-admin' }, 'installation.create', teamObject))
      .to.deep.equal({ allow: false, reason: 'membership_unavailable' });
    expect(await authorizer.check({ orgId: VECTORS.orgId, userId: 'u-admin' }, 'installation.create', teamObject))
      .to.deep.equal({ allow: true });
  });

  it('AZV-1: the role is read on every check, never cached', async () => {
    const facts = vectorWorld();
    const authorizer = authorizerOver(facts);
    const actor = { orgId: VECTORS.orgId, userId: 'u-admin' };
    await authorizer.check(actor, 'catalog.read', { orgId: VECTORS.orgId, type: 'catalog-item' });
    await authorizer.check(actor, 'catalog.read', { orgId: VECTORS.orgId, type: 'catalog-item' });
    expect(facts.roles.reads).to.equal(2);
  });
});
