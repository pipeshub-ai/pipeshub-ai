/**
 * G-41 (GA-21): `HarnessGroupMembership` reads the actor's membership of the
 * org's user groups live from MongoDB, so a removal is seen by the very next
 * admission (no cache). Only the org's own, live groups count.
 *
 *   MONGO_IT_URI="mongodb://localhost:<port>/x?replicaSet=rs0" REPLICA_SET_AVAILABLE=true \
 *     TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
 *     --require ts-node/register --exit --timeout 60000 tests/modules/agent-harness/ga21-group-membership.mongo.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model, Types } from 'mongoose';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { HarnessGroupMembership } from '../../../src/libs/repositories/agent-harness/group-membership/mongo';
import { UserGroups } from '../../../src/modules/user_management/schema/userGroup.schema';

registerHarnessModels([UserGroups] as unknown as Array<Model<never>>);

const id = (): string => new Types.ObjectId().toHexString();

describe('GA-21: group membership on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const orgA = id();
  const orgB = id();
  const alice = id();
  const bob = id();
  const groups = { agents: id(), coders: id(), deleted: id(), otherOrg: id() };
  const membership = new HarnessGroupMembership();

  beforeEach(async () => {
    await resetHarnessMongo();
    const create = (groupId: string, orgId: string, users: string[], isDeleted = false) =>
      UserGroups.create({
        _id: new Types.ObjectId(groupId),
        orgId: new Types.ObjectId(orgId),
        name: `group-${groupId}`,
        type: 'custom',
        users: users.map((user) => new Types.ObjectId(user)),
        isDeleted,
      });
    await create(groups.agents, orgA, [alice]);
    await create(groups.coders, orgA, [bob]);
    await create(groups.deleted, orgA, [alice, bob], true);
    await create(groups.otherOrg, orgB, [bob]);
  });

  it("GA-21: a member of any listed group of the org is admitted; removal is seen by the next read", async () => {
    expect(await membership.memberOfAny(orgA, alice, [groups.agents])).to.equal(true);
    expect(await membership.memberOfAny(orgA, alice, [groups.coders, groups.agents])).to.equal(true);
    await UserGroups.updateOne({ _id: groups.agents }, { $pull: { users: new Types.ObjectId(alice) } });
    expect(await membership.memberOfAny(orgA, alice, [groups.agents])).to.equal(false);
  });

  it("GRP-14: a deleted group, another org's group, an id that is no group or no id at all admits nobody", async () => {
    expect(await membership.memberOfAny(orgA, bob, [groups.deleted])).to.equal(false);
    expect(await membership.memberOfAny(orgA, bob, [groups.otherOrg])).to.equal(false);
    expect(await membership.memberOfAny(orgA, bob, ['not-an-object-id'])).to.equal(false);
    expect(await membership.memberOfAny(orgA, 'not-a-user', [groups.coders])).to.equal(false);
    expect(await membership.memberOfAny('not-an-org', bob, [groups.coders])).to.equal(false);
    expect(await membership.memberOfAny(orgA, bob, [])).to.equal(false);
  });

  it("GRP-15: unknownGroups answers the ids that are not the org's live groups (what a save refuses)", async () => {
    expect(
      await membership.unknownGroups(orgA, [groups.agents, groups.deleted, groups.otherOrg, 'nope']),
    ).to.deep.equal([groups.deleted, groups.otherOrg, 'nope']);
  });
});
