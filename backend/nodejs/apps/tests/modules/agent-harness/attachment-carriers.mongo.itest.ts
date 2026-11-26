/**
 * CLEANUP-G5: which conversation's message carries an upload, read from
 * `chatSessionMessages` on a MongoDB replica set, never across orgs. Run as
 * execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Types } from 'mongoose';
import { HarnessAttachmentCarriers } from '../../../src/libs/repositories/agent-harness/attachment-carriers/mongo';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { connectHarnessMongo, disconnectHarnessMongo, resetHarnessMongo } from './support/harness-mongo';

describe('CLEANUP-G5 attachment carriers on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('names the conversation of a message that carries each upload; another org\'s message never counts', async () => {
    const org = new Types.ObjectId();
    const other = new Types.ObjectId();
    const session = new Types.ObjectId();
    const message = (orgId: Types.ObjectId, sessionId: Types.ObjectId, seq: number, recordIds: string[]) => ({
      sessionId,
      orgId,
      seq,
      messageType: 'user_query',
      content: 'q',
      attachments: recordIds.map((recordId) => ({ recordId, recordName: `${recordId}.txt`, mimeType: 'text/plain' })),
    });
    await ChatSessionMessage.create([
      message(org, session, 1, ['a', 'b']),
      message(org, session, 2, ['b']),
      message(other, new Types.ObjectId(), 1, ['c']),
    ]);
    const carried = await new HarnessAttachmentCarriers().carriedBy(String(org), ['a', 'b', 'c', 'd']);
    expect([...carried.entries()].sort()).to.deep.equal([
      ['a', String(session)],
      ['b', String(session)],
    ]);
    expect((await new HarnessAttachmentCarriers().carriedBy('not-an-id', ['a'])).size).to.equal(0);
  });
});
