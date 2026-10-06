import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Types } from 'mongoose';
import sinon from 'sinon';
import { ChatContentLoader } from '../../../src/modules/authz/loaders/chat-content.loader';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';

const uri = process.env.PCC_MONGO_URI;

interface Plan {
  queryPlanner: { winningPlan: unknown };
}

const winningPlan = (plan: Plan): string =>
  JSON.stringify(plan.queryPlanner.winningPlan);

(uri ? describe : describe.skip)(
  'chat session message indexes and chat-content loader against a real MongoDB',
  function () {
    this.timeout(30_000);
    const orgId = new Types.ObjectId();
    const otherOrg = new Types.ObjectId();
    const sessionX = new Types.ObjectId();
    const sessionY = new Types.ObjectId();
    const author = new Types.ObjectId();
    const loader = new ChatContentLoader();
    let seq = 0;

    const turn = (over: Record<string, unknown>) => ({
      sessionId: sessionX,
      orgId,
      seq: seq++,
      messageType: 'user_query',
      content: 'q',
      authorUserId: author,
      ...over,
    });

    before(async () => {
      await mongoose.connect(uri as string);
      await ChatSessionMessage.init();
    });

    after(async () => {
      await ChatSessionMessage.deleteMany({
        orgId: { $in: [orgId, otherOrg] },
      });
      await mongoose.disconnect();
    });

    it('declares the two partial indexes with $type, not $exists', async () => {
      const indexes = await ChatSessionMessage.collection.indexes();
      const byKey = (key: Record<string, number>) =>
        indexes.find((i) => JSON.stringify(i.key) === JSON.stringify(key));
      expect(
        byKey({ orgId: 1, 'attachments.recordId': 1 })?.partialFilterExpression,
      ).to.deep.equal({
        'attachments.recordId': { $type: 'string' },
      });
      expect(
        byKey({ sessionId: 1, runId: 1 })?.partialFilterExpression,
      ).to.deep.equal({ runId: { $type: 'string' } });
    });

    describe('loader', () => {
      before(async () => {
        await ChatSessionMessage.insertMany([
          turn({ attachments: [{ recordId: 'rec-1' }], filesShared: true }),
          turn({
            sessionId: sessionY,
            attachments: [{ recordId: 'rec-1' }],
            filesShared: false,
          }),
          turn({
            sessionId: sessionY,
            attachments: [{ recordId: 'rec-only-y' }],
            filesShared: true,
          }),
          turn({
            orgId: otherOrg,
            attachments: [{ recordId: 'rec-1' }],
            filesShared: true,
          }),
          turn({
            messageType: 'bot_response',
            attachments: [{ recordId: 'rec-1' }],
          }),
          turn({ runId: 'run-1', shareToolResults: true }),
          turn({
            sessionId: sessionY,
            runId: 'run-1',
            shareToolResults: false,
          }),
          turn({ orgId: otherOrg, runId: 'run-other', shareToolResults: true }),
        ]);
      });

      it('finds only this org user turns, across chats when no conversation is given', async () => {
        const rows = await loader.loadAttachmentContext(
          orgId.toString(),
          'rec-1',
        );
        expect(rows.map((r) => r.sessionId).sort()).to.deep.equal(
          [sessionX.toString(), sessionY.toString()].sort(),
        );
      });

      it('restricts to the given conversation, so a record only in chat Y finds nothing through X', async () => {
        expect(
          await loader.loadAttachmentContext(
            orgId.toString(),
            'rec-only-y',
            sessionX.toString(),
          ),
        ).to.deep.equal([]);
        const viaY = await loader.loadAttachmentContext(
          orgId.toString(),
          'rec-only-y',
          sessionY.toString(),
        );
        expect(viaY).to.have.length(1);
        expect(viaY[0]).to.deep.include({
          sessionId: sessionY.toString(),
          authorUserId: author.toString(),
          filesShared: true,
        });
      });

      it('does not see another org rows', async () => {
        const rows = await loader.loadAttachmentContext(
          otherOrg.toString(),
          'rec-1',
        );
        expect(rows).to.have.length(1);
        expect(
          await loader.loadAttachmentContext(orgId.toString(), 'rec-nope'),
        ).to.deep.equal([]);
      });

      it('caps at 20 rows', async () => {
        await ChatSessionMessage.insertMany(
          Array.from({ length: 25 }, () =>
            turn({
              sessionId: new Types.ObjectId(),
              attachments: [{ recordId: 'rec-many' }],
              filesShared: true,
            }),
          ),
        );
        expect(
          await loader.loadAttachmentContext(orgId.toString(), 'rec-many'),
        ).to.have.length(20);
      });

      it('reads the user turn of one run in one chat', async () => {
        expect(
          await loader.loadArtifactContext(
            orgId.toString(),
            sessionX.toString(),
            'run-1',
          ),
        ).to.deep.equal({
          authorUserId: author.toString(),
          shareToolResults: true,
        });
        expect(
          await loader.loadArtifactContext(
            orgId.toString(),
            sessionY.toString(),
            'run-1',
          ),
        ).to.deep.include({ shareToolResults: false });
        expect(
          await loader.loadArtifactContext(
            orgId.toString(),
            sessionX.toString(),
            'run-other',
          ),
        ).to.equal(null);
        expect(
          await loader.loadArtifactContext(
            orgId.toString(),
            sessionX.toString(),
            undefined,
          ),
        ).to.equal(null);
      });
    });

    describe('query plans', () => {
      afterEach(() => sinon.restore());

      it('the loader attachment query uses the partial attachments.recordId index', async () => {
        const find = sinon.spy(ChatSessionMessage, 'find') as unknown as sinon.SinonSpy<unknown[]>;
        await loader.loadAttachmentContext(orgId.toString(), 'rec-1');
        const plan = (await ChatSessionMessage.find(
          find.firstCall.args[0] as Record<string, unknown>,
        ).explain('queryPlanner')) as unknown as Plan;
        expect(winningPlan(plan)).to.include(
          '"indexName":"orgId_1_attachments.recordId_1"',
        );
      });

      it('the loader run query uses the partial runId index', async () => {
        const findOne = sinon.spy(ChatSessionMessage, 'findOne') as unknown as sinon.SinonSpy<unknown[]>;
        await loader.loadArtifactContext(
          orgId.toString(),
          sessionX.toString(),
          'run-1',
        );
        const plan = (await ChatSessionMessage.find(
          findOne.firstCall.args[0] as Record<string, unknown>,
        ).explain('queryPlanner')) as unknown as Plan;
        expect(winningPlan(plan)).to.include(
          '"indexName":"sessionId_1_runId_1"',
        );
      });
    });
  },
);
