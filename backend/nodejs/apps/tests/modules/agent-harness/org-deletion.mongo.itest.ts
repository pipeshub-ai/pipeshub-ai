/**
 * `OrgDeletionData` (CLEANUP-G2) on a MongoDB replica set: the Mongo world of `describeOrgDeletionDataContract`,
 * beside the PostgreSQL one in `org-deletion.pg.itest.ts` (WP114-FU-MONGO). Rows are seeded through the
 * platform's own models and read back the same way.
 */
import 'reflect-metadata';
import { Model, Types } from 'mongoose';
import { orgDeletionData } from '../../../src/libs/repositories/agent-harness/org-deletion';
import { inMongoDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { describeOrgDeletionDataContract, SeedUser } from './support/org-deletion-data-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';

registerHarnessModels([Users, ChatSession] as unknown as Array<Model<never>>);

describe('org and person deletion data on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  let docStore: string | undefined;
  before(() => {
    // Unset is MongoDB, the default store.
    docStore = process.env.DOC_STORE;
    delete process.env.DOC_STORE;
  });
  after(() => {
    if (docStore === undefined) delete process.env.DOC_STORE;
    else process.env.DOC_STORE = docStore;
  });

  // "A later one is younger": each user's account age is set, a millisecond apart, not left to the clock.
  let tick = Date.UTC(2026, 0, 1);
  const seedUser = async (orgId: string, opts: SeedUser = {}): Promise<string> => {
    const id = new Types.ObjectId();
    tick += 1;
    await new Users({
      _id: id,
      orgId: new Types.ObjectId(orgId),
      fullName: opts.fullName,
      email: opts.email ?? `${String(id)}@example.com`,
      kind: opts.kind ?? 'human',
      role: opts.role ?? 'member',
      isDisabled: opts.disabled === true,
      isDeleted: false,
      createdAt: new Date(tick),
      updatedAt: new Date(tick),
    }).save({ timestamps: false });
    return String(id);
  };

  describeOrgDeletionDataContract('mongodb', async () => {
    await resetHarnessMongo();
    return {
      data: orgDeletionData(),
      transact: (_orgId, work) => inMongoDurableTransaction(work),
      org: async () => String(new Types.ObjectId()),
      user: seedUser,
      conversation: async (orgId, ownerId, opts = {}) => {
        const owner = new Types.ObjectId(ownerId);
        const session = await ChatSession.create({
          orgId: new Types.ObjectId(orgId),
          userId: owner,
          initiator: owner,
          title: 'c',
          isShared: opts.sharedWith !== undefined,
          sharedWith: (opts.sharedWith ?? []).map((userId) => ({ userId: new Types.ObjectId(userId), accessLevel: 'read' })),
          isDeleted: opts.deleted === true,
        });
        return String(session._id);
      },
      conversationState: async (orgId, id) => {
        const row = await ChatSession.findOne({ _id: new Types.ObjectId(id), orgId: new Types.ObjectId(orgId) })
          .select('+erasureRequestedAt')
          .lean();
        return {
          owner: String(row!.userId),
          initiator: String(row!.initiator),
          sharedWith: (row!.sharedWith ?? []).map((member) => String(member.userId)),
          isDeleted: row!.isDeleted === true,
          ...(typeof row!.erasureRequestedAt === 'number' ? { erasureRequestedAt: row!.erasureRequestedAt } : {}),
        };
      },
      isDisabled: async (orgId, userId) => {
        const row = await Users.findOne({ _id: new Types.ObjectId(userId), orgId: new Types.ObjectId(orgId) })
          .select('isDisabled')
          .lean();
        return row?.isDisabled === true;
      },
    };
  });
});
