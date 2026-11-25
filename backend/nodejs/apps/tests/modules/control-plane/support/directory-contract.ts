/**
 * MP and AD: what every `MembershipPort` and `AgentDirectory` do (03-interfaces
 * §5.5); CPD-9 runs it on the Node HTTP clients of the Python routes and on
 * their caching decorators (which must never cache a throw).
 */
import { expect } from 'chai';
import type { AgentDirectory, MembershipPort } from '../../../../src/modules/control-plane/ports/directory';
import type { TeamMembership } from '../../../../src/modules/control-plane/domain/resolution';
import { ORG_A, ORG_B, rejection } from './cp-objects';

export interface DirectoryWorld {
  readonly membership: MembershipPort;
  readonly agents: AgentDirectory;
  setTeams(orgId: string, userId: string, teams: TeamMembership[]): Promise<void>;
  /** An agent `runners` may run and `editors` may edit. */
  setAgent(orgId: string, agentKey: string, agent: { definitionVersion: number; runners: string[]; editors: string[]; ownerTeam: string | null }): Promise<void>;
  /** Makes the graph behind both ports unreachable, or reachable again. */
  setAvailable(available: boolean): Promise<void>;
}

const actor = (userId: string, orgId = ORG_A) => ({ orgId, userId });

export function describeDirectoryContract(name: string, make: () => Promise<DirectoryWorld>): void {
  describe(`MembershipPort and AgentDirectory contract (MP, AD): ${name}`, () => {
    let world: DirectoryWorld;

    beforeEach(async () => {
      world = await make();
      await world.setTeams(ORG_A, 'u1', [{ teamId: 't9', role: 'OWNER' }]);
      await world.setTeams(ORG_B, 'u1', [{ teamId: 't1', role: 'READER' }]);
      await world.setAgent(ORG_A, 'a7', { definitionVersion: 9, runners: ['u1', 'u2'], editors: ['u1'], ownerTeam: 't9' });
    });

    it('MP-1: a user\'s teams in that org only; an unknown user has none', async () => {
      expect(await world.membership.teamsOf(ORG_A, 'u1')).to.deep.equal([{ teamId: 't9', role: 'OWNER' }]);
      expect(await world.membership.teamsOf(ORG_B, 'u1')).to.deep.equal([{ teamId: 't1', role: 'READER' }]);
      expect(await world.membership.teamsOf(ORG_A, 'nobody')).to.deep.equal([]);
    });

    it('MP-2: unreadable membership throws, never an empty list', async () => {
      await world.setAvailable(false);
      expect(await rejection(world.membership.teamsOf(ORG_A, 'u1'))).to.be.an('error');
    });

    it('AD-1: an allowed run gets its definition version; a denied one is null', async () => {
      expect(await world.agents.authorizeRun(ORG_A, 'a7', actor('u2'))).to.deep.equal({ agentKey: 'a7', definitionVersion: 9 });
      expect(await world.agents.authorizeRun(ORG_A, 'a7', actor('u3'))).to.equal(null);
      expect(await world.agents.canEdit(ORG_A, 'a7', actor('u1'))).to.equal(true);
      expect(await world.agents.canEdit(ORG_A, 'a7', actor('u2'))).to.equal(false);
      expect(await world.agents.ownerTeam(ORG_A, 'a7')).to.equal('t9');
    });

    it('AD-2: an unreachable directory throws (admission fails closed), never null', async () => {
      await world.setAvailable(false);
      expect(await rejection(world.agents.authorizeRun(ORG_A, 'a7', actor('u2')))).to.be.an('error');
      expect(await rejection(world.agents.canEdit(ORG_A, 'a7', actor('u1')))).to.be.an('error');
      await world.setAvailable(true);
      expect(await world.agents.authorizeRun(ORG_A, 'a7', actor('u2'))).to.not.equal(null);
    });

    it('AD-3: another org\'s agent is unknown here', async () => {
      expect(await world.agents.authorizeRun(ORG_B, 'a7', actor('u2', ORG_B))).to.equal(null);
      expect(await world.agents.authorizeRun(ORG_A, 'a7', actor('u2', ORG_B))).to.equal(null);
      expect(await world.agents.canEdit(ORG_B, 'a7', actor('u1', ORG_B))).to.equal(false);
      expect(await world.agents.ownerTeam(ORG_B, 'a7')).to.equal(null);
    });
  });
}
