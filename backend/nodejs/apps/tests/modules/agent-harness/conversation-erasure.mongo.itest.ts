/**
 * The conversation module's erasure and retention adapters on a MongoDB
 * replica set, seeded through the models the conversation module writes with.
 *
 *   MONGO_IT_URI="mongodb://localhost:<port>/x?replicaSet=rs0" REPLICA_SET_AVAILABLE=true \
 *     TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
 *     --require ts-node/register --exit --timeout 60000 tests/modules/agent-harness/conversation-erasure.mongo.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { sessionMessages } from './support/session-messages';
import mongoose, { ClientSession, Model, Types } from 'mongoose';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { FakeChatAttachments } from './support/chat-attachments';
import { citationRecordPrefix, seedMongoConversations } from './support/conversation-seeds';
import {
  ConversationRemains,
  describeConversationEraserContract,
  describeConversationRetentionContract,
  describeConversationStateLookupContract,
} from './support/conversation-erasure-contract';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { HarnessConversationEraser } from '../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { HarnessConversationRetention } from '../../../src/libs/repositories/agent-harness/conversation-retention/mongo';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import { attachMessages, buildConversationResponse } from '../../../src/modules/enterprise_search/utils/utils';
import { hex } from './support/erasure-backfill-contract';

registerHarnessModels([Citation] as unknown as Array<Model<never>>);

const LEGACY = ['conversations', 'agentconversations'];

const empty = async (): Promise<void> => {
  await resetHarnessMongo();
  for (const name of LEGACY) {
    await mongoose.connection.collection(name).deleteMany({});
  }
};

const removedAttachmentsIn = async (orgId: string, sessionId: string): Promise<string[]> => {
  const messages = await ChatSessionMessage.find(
    { sessionId: new Types.ObjectId(sessionId), orgId: new Types.ObjectId(orgId) },
    { attachments: 1 },
  ).lean();
  return messages
    .flatMap((message) => message.attachments ?? [])
    .filter((attachment) => attachment.removed === true)
    .map((attachment) => attachment.recordId)
    .sort();
};

const remains = async (orgId: string, sessionId: string): Promise<ConversationRemains> => {
  const [org, session] = [new Types.ObjectId(orgId), new Types.ObjectId(sessionId)];
  let legacyCopies = 0;
  for (const name of LEGACY) {
    legacyCopies += await mongoose.connection.collection(name).countDocuments({ _id: session, orgId: org });
  }
  return {
    sessions: await ChatSession.countDocuments({ _id: session, orgId: org }),
    messages: await ChatSessionMessage.countDocuments({ sessionId: session, orgId: org }),
    citations: await Citation.countDocuments({
      'metadata.orgId': orgId,
      'metadata.recordId': { $regex: `^${citationRecordPrefix(sessionId)}` },
    }),
    legacyCopies,
  };
};

describe('Conversation erasure ports on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeConversationStateLookupContract('HarnessConversationEraser', async () => {
    await empty();
    return { lookup: new HarnessConversationEraser(new FakeChatAttachments()), seed: seedMongoConversations };
  });

  describeConversationEraserContract('HarnessConversationEraser', async () => {
    await empty();
    const attachments = new FakeChatAttachments();
    return {
      eraser: new HarnessConversationEraser(attachments),
      seed: seedMongoConversations,
      remains,
      attachments,
      removedIn: removedAttachmentsIn,
    };
  });

  it('QA15G-8: the other conversation, read as the conversation route reads it, shows the erased upload as removed', async () => {
    await empty();
    await ChatSessionMessage.createIndexes();
    const [org, erased, sibling, owner] = [hex(), hex(), hex(), hex()];
    const [shared, kept] = [hex(), hex()];
    const attachments = new FakeChatAttachments();
    attachments.upload(shared, { orgId: org, ownerId: owner, content: 'a' });
    attachments.upload(kept, { orgId: org, ownerId: owner, content: 'b' });
    await seedMongoConversations([
      { orgId: org, sessionId: erased, deleted: true, ownerId: owner, attachments: [{ recordId: shared }] },
      { orgId: org, sessionId: sibling, deleted: false, ownerId: owner, attachments: [{ recordId: shared }, { recordId: kept }] },
    ]);

    await new HarnessConversationEraser(attachments).erase(org, erased);

    const session = await ChatSession.findOne({ _id: new Types.ObjectId(sibling) }).lean();
    const messages = await sessionMessages(new Types.ObjectId(sibling), { populateCitations: true });
    const shaped = attachMessages(session, messages);
    const response = buildConversationResponse(
      shaped,
      owner,
      { page: 1, limit: 20, skip: 0, totalMessages: messages.length, hasNextPage: false, hasPrevPage: false },
      shaped.messages,
    );
    const chips = response.messages.flatMap((message) => message.attachments ?? []);
    expect(chips.map(({ recordId, removed }) => ({ recordId, removed }))).to.deep.equal([
      { recordId: shared, removed: true },
      { recordId: kept, removed: undefined },
    ]);
  });

  describeConversationRetentionContract<ClientSession>('HarnessConversationRetention', async () => {
    await empty();
    return {
      retention: new HarnessConversationRetention(),
      seed: seedMongoConversations,
      inTransaction: (work) => inMongoTransaction(work),
    };
  });
});
