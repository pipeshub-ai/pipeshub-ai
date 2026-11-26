/**
 * WP-73's store ports as every adapter keeps them (C-2 for Node): the
 * actor's group membership (G-41). Each adapter runs its
 * suite: in memory in the unit lane, on a replica set and on PostgreSQL in
 * the itest lane.
 */
import { expect } from 'chai';
import { Types } from 'mongoose';
import type { ActorGroupMembership } from '../../../../src/modules/agent-harness/ports/group-access';

export interface GroupWorld {
  membership: ActorGroupMembership;
  /** A live group of `orgId` holding `users`. */
  group(orgId: string, groupId: string, users: string[]): Promise<void>;
  removeFrom(orgId: string, groupId: string, userId: string): Promise<void>;
}

export function describeActorGroupMembershipContract(
  name: string,
  make: () => Promise<GroupWorld>,
): void {
  describe(`ActorGroupMembership contract: ${name}`, () => {
    const id = (): string => new Types.ObjectId().toHexString();
    const [orgA, orgB, alice, bob, agents, coders, foreign] = [id(), id(), id(), id(), id(), id(), id()];
    let world: GroupWorld;
    beforeEach(async () => {
      world = await make();
      await world.group(orgA, agents, [alice]);
      await world.group(orgA, coders, [bob]);
      await world.group(orgB, foreign, [bob]);
    });

    it('GRP-28: a member of any listed group of the org is in; a removal is seen by the next read', async () => {
      const { membership } = world;
      expect(await membership.memberOfAny(orgA, alice, [coders, agents])).to.equal(true);
      expect(await membership.memberOfAny(orgA, bob, [agents])).to.equal(false);
      await world.removeFrom(orgA, agents, alice);
      expect(await membership.memberOfAny(orgA, alice, [agents])).to.equal(false);
    });

    it("GRP-28: another org's group and an empty list admit nobody", async () => {
      const { membership } = world;
      expect(await membership.memberOfAny(orgA, bob, [foreign])).to.equal(false);
      expect(await membership.memberOfAny(orgA, bob, [])).to.equal(false);
    });
  });
}
