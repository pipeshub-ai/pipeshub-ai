import type { Tx } from '../../../src/libs/repositories/uow';
/**
 * SS1 on a MongoDB replica set (run as execution-store.mongo.itest.ts says):
 * sharing and unsharing a conversation journal who may read it on the
 * session's root stream, in the share's own transaction, through the audited
 * and sealed journal; and a session that never ran durably still imports its
 * legacy history as one that never ran (PE-7, PE-10, PE-5/PE-6 atomicity),
 * and the SessionParticipantRecorder contract on the Mongo journal.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { setUserRepository, type UserRepository } from '../../../src/libs/repositories/user';
import { appendMessages } from '../../../src/modules/enterprise_search/utils/utils';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import * as controller from '../../../src/modules/enterprise_search/controller/es_controller';
import { IAMServiceCommand } from '../../../src/libs/commands/iam/iam.service.command';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SessionParticipantJournal } from '../../../src/modules/agent-harness/application/session-participant-journal';
import { MongoJournalStore } from '../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { SessionParticipantRecorder } from '../../../src/modules/agent-harness/ports/session-participants';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { testCipher } from './support/data-protection';
import { describeSessionParticipantRecorderContract } from './support/session-participant-recorder-contract';
import { inMongoDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const RECIPIENT = String(new Types.ObjectId());
const COLLEAGUE = String(new Types.ObjectId());

const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never;

describe('SS1 participant events on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    // The share route checks each user with IAM and syncs record permissions with Python.
    sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200 } as never);
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as never);
    // Main's share (#4453) admits only users of the org; every recipient here is one.
    setUserRepository({
      listLiveByIds: async (_orgId: string, ids: string[]) => ids.map((_id) => ({ _id })),
    } as unknown as UserRepository);
  });
  afterEach(() => {
    sinon.restore();
    setUserRepository();
  });

  const harness = (): ContractHarness => {
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout(V2);
    return h;
  };

  const audit = new MongoAuditChainStore();
  const journal = new AuditedJournalStore(
    new SealedJournalStore(new MongoJournalStore(), testCipher()),
    audit,
  );
  const recorder = new SessionParticipantJournal<Tx>(
    journal,
    new JournalService(new MongoPayloadStore(), testCipher()),
  );

  const legacySession = async (h: ContractHarness): Promise<string> => {
    const session = await ChatSession.create({
      orgId: h.tenant.orgId,
      userId: h.tenant.actorId,
      initiator: h.tenant.actorId,
      title: 'Legacy conversation',
      sessionType: 'chat',
      status: 'Complete',
    });
    const sessionId = String(session._id);
    await appendMessages(sessionId, h.tenant.orgId, [
      { messageType: 'user_query', content: 'What is the leave policy?' },
      { messageType: 'bot_response', content: 'Twenty-five days.', citations: [] },
    ] as unknown as IMessage[]);
    return sessionId;
  };

  const call = async (
    handler: ReturnType<typeof controller.shareConversationById>,
    h: ContractHarness,
    sessionId: string,
    body: Record<string, unknown>,
  ): Promise<unknown> => {
    const next = sinon.stub();
    const res = { status: () => res, json: () => res };
    await handler(
      {
        headers: { authorization: 'Bearer owner' },
        params: { conversationId: sessionId },
        body,
        query: {},
        user: { userId: h.tenant.actorId, orgId: h.tenant.orgId },
        context: { requestId: 'req-share' },
      } as never,
      res as never,
      next as never,
    );
    return next.firstCall?.args[0];
  };

  const share = (h: ContractHarness, sessionId: string, body: Record<string, unknown>, using: SessionParticipantRecorder<Tx> = recorder) =>
    call(controller.shareConversationById(appConfig, () => using), h, sessionId, body);
  const unshare = (h: ContractHarness, sessionId: string, userIds: string[]) =>
    call(controller.unshareConversationById(appConfig, () => recorder), h, sessionId, { userIds });

  const stream = async (h: ContractHarness, sessionId: string) =>
    journal.readStream(h.tenant.orgId, sessionId, 0, 100);

  it('PE-10: share, re-share as collaborator, unshare: one event per change, in order, no run, audit-chained', async () => {
    const h = harness();
    const sessionId = await legacySession(h);
    const by = `p_${h.tenant.actorId}`;

    expect(await share(h, sessionId, { userIds: [RECIPIENT, COLLEAGUE] })).to.equal(undefined);
    expect(await share(h, sessionId, { userIds: [RECIPIENT], accessLevel: 'read' })).to.equal(undefined);
    expect(await share(h, sessionId, { userIds: [COLLEAGUE], accessLevel: 'write' })).to.equal(undefined);
    expect(await unshare(h, sessionId, [RECIPIENT, COLLEAGUE])).to.equal(undefined);

    const events = await stream(h, sessionId);
    const sorted = [RECIPIENT, COLLEAGUE].sort();
    expect(events.map((e) => [e.seq, e.data])).to.deep.equal([
      [1, { by, subject: `p_${sorted[0]}`, role: 'viewer', prevRole: 'none' }],
      [2, { by, subject: `p_${sorted[1]}`, role: 'viewer', prevRole: 'none' }],
      [3, { by, subject: `p_${COLLEAGUE}`, role: 'collaborator', prevRole: 'viewer' }],
      ...[RECIPIENT, COLLEAGUE].sort().map((user, index) => [
        4 + index,
        { by, subject: `p_${user}`, role: 'none', prevRole: user === COLLEAGUE ? 'collaborator' : 'viewer' },
      ]),
    ]);
    for (const event of events) {
      expect(event).to.include({ type: 'session.participant_changed', streamId: sessionId, writer: 'node', visibility: 'shared', critical: false });
      expect(event.runId).to.equal(undefined);
    }
    const records = await audit.read(h.tenant.orgId, 0, 100);
    expect(records.map((r) => [r.kind, r.journalSeq, r.runId, r.fields])).to.deep.equal(
      events.map((e) => ['session.participant_changed', e.seq, null, e.data]),
    );
    const stored = await ChatSession.findById(sessionId).lean();
    expect(stored?.isShared).to.equal(false);
  });

  it('PE-7: a session shared before its first durable run imports its legacy history as one that never ran', async () => {
    const h = harness();
    const sessionId = await legacySession(h);
    expect(await share(h, sessionId, { userIds: [RECIPIENT] })).to.equal(undefined);

    const admitted = await h.admit({
      sessionId,
      input: { query: 'And sick leave?', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });

    expect(admitted.imported).to.not.equal(undefined);
    expect(admitted.imported?.mismatch).to.equal(undefined);
    expect(admitted.imported?.replacesHistory).to.equal(false);
    expect((await stream(h, sessionId)).map((e) => e.type)).to.deep.equal([
      'session.participant_changed',
      'session.imported',
      'x.pipeshub.history.labelled',
      'run.admitted',
    ]);
  });

  it('PE-5: a journal failure aborts the share: the conversation keeps its participants', async () => {
    const h = harness();
    const sessionId = await legacySession(h);
    const failing: SessionParticipantRecorder<Tx> = {
      participantsChanged: async () => {
        throw new Error('journal unavailable');
      },
      forked: async () => undefined,
    };
    const error = await share(h, sessionId, { userIds: [RECIPIENT] }, failing);
    expect((error as Error | undefined)?.message).to.equal('journal unavailable');
    const stored = await ChatSession.findById(sessionId).lean();
    expect(stored?.isShared ?? false).to.equal(false);
    expect(stored?.sharedWith ?? []).to.have.length(0);
    expect(await stream(h, sessionId)).to.have.length(0);
  });

  describeSessionParticipantRecorderContract<Tx>('mongo journal', async () => ({
    recorder,
    journal,
    audit,
    transact: inMongoDurableTransaction,
  }));
});
