/**
 * `MongoApproverPeople` on a MongoDB replica set: who is a live person of the org, over users created through the
 * user-management models. PostgreSQL's twin is `approver-directory.pg.itest.ts`; the directory's own rules are its
 * contract's. Run as execution-store.mongo.itest.ts says.
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
import {
  HarnessApproverDirectory,
  MongoApproverPeople,
} from '../../../src/libs/repositories/agent-harness/approver-directory/mongo';
import { Users } from '../../../src/modules/user_management/schema/users.schema';

registerHarnessModels([Users] as unknown as Array<Model<never>>);

const oid = (): string => new Types.ObjectId().toHexString();

interface Person {
  admin?: boolean;
  deleted?: boolean;
  service?: boolean;
}

async function person(orgId: string, opts: Person = {}): Promise<string> {
  const id = oid();
  await Users.create({
    _id: new Types.ObjectId(id),
    orgId: new Types.ObjectId(orgId),
    email: `${id}@example.com`,
    role: opts.admin === true ? 'admin' : 'member',
    kind: opts.service === true ? 'service' : 'human',
    isDeleted: opts.deleted === true,
  });
  return id;
}

describe('approver people on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  const people = new MongoApproverPeople();

  it('isLiveUser: a live user of the org, a service account included', async () => {
    const [org, other] = [oid(), oid()];
    const ana = await person(org);
    const bot = await person(org, { service: true });
    const left = await person(org, { deleted: true });
    expect(await people.isLiveUser(org, ana)).to.equal(true);
    expect(await people.isLiveUser(org, bot)).to.equal(true);
    expect(await people.isLiveUser(org, left)).to.equal(false);
    expect(await people.isLiveUser(other, ana)).to.equal(false);
    expect(await people.isLiveUser(org, 'bad')).to.equal(false);
    expect(await people.isLiveUser('bad', ana)).to.equal(false);
    // QA-26: the directory answers membership through it.
    const directory = new HarnessApproverDirectory(people);
    expect(await directory.isMember(org, ana)).to.equal(true);
    expect(await directory.isMember(org, left)).to.equal(false);
    expect(await directory.isApprover(org, ana)).to.equal(false);
  });
});
