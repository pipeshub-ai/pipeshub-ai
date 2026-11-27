/**
 * PERF-E2: the durable path's lean conversation writer stores what the legacy
 * writer it replaced stored (the legacy chat API's shape: the session, its
 * messages, their citations and the history ledger), in fewer commands. The
 * replaced writer is kept here, as it was, as the reference. Runs on a
 * replica set as execution-store.mongo.itest.ts says.
 *
 * What a failure's error message carries about its run (code, servers, stop,
 * outcome) is one mapping both writers call, `runFailureFields`: the test
 * compares how each writer stores it, not two copies of the mapping.
 *
 * The history a model is sent seals each row with its id and the version of
 * its text, so two writes of one conversation never share a ledger digest:
 * each writer's ledger must be the one its own stored rows give instead.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { ClientSession, Types } from 'mongoose';
import type { CommandStartedEvent } from 'mongodb';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import { HarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import {
  HarnessConversationWriter,
  runFailureFields,
} from '../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import {
  appendMessages,
  buildUserQueryMessage,
  markConversationFailed,
  saveCompleteConversation,
} from '../../../src/modules/enterprise_search/utils/utils';
import { sealedHistoryTurns } from '../../../src/modules/enterprise_search/utils/utils';
import { EMPTY_LEDGER, extendLedger, HistoryLedger } from '../../../src/modules/agent-harness/domain/session-import';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { RunTranscript, UserTurn } from '../../../src/modules/agent-harness/ports/conversation-writer';
import {
  IAIModel,
  IAIResponse,
  IChatSessionDocument,
  IMessage,
  IMessagePart,
} from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { connectHarnessMongo, disconnectHarnessMongo, resetHarnessMongo } from './support/harness-mongo';

const ORG = new Types.ObjectId().toString();
const OWNER = new Types.ObjectId().toString();
const COLLABORATOR = new Types.ObjectId().toString();
const MODEL = { modelKey: 'm-1', modelName: 'gpt-x', modelProvider: 'openai', chatMode: 'quick' };

/** The writer the durable path used before PERF-E2, as it was. */
class LegacyWriter {
  private async accounted<T>(sessionId: string, tx: ClientSession, write: () => Promise<T>): Promise<T> {
    const row = async () =>
      (await ChatSession.findById(sessionId).select('+nextSeq +harnessLedger').session(tx).lean()) as {
        nextSeq?: number;
        harnessLedger?: HistoryLedger;
      } | null;
    const before = await row();
    const result = await write();
    const ledger = before?.harnessLedger;
    if (before === null || !ledger) return result;
    const plain = { seq: ledger.seq, digest: ledger.digest, ...(ledger.diverged === true ? { diverged: true } : {}) };
    const lastSeq = (await row())?.nextSeq ?? 0;
    if (lastSeq === ledger.seq) return result;
    let next: HistoryLedger;
    if (ledger.diverged === true || (before.nextSeq ?? 0) !== ledger.seq) {
      next = { ...plain, diverged: true };
    } else {
      const written = (await ChatSessionMessage.find({ sessionId, seq: { $gte: ledger.seq + 1 } })
        .sort({ seq: 1 })
        .session(tx)
        .lean()) as unknown as Array<IMessage & { seq: number }>;
      next = extendLedger(
        plain,
        written.map((message) => ({
          seq: message.seq,
          turns: JSON.parse(JSON.stringify(sealedHistoryTurns(message))) as Array<Record<string, unknown>>,
          provenance: null,
        })),
        lastSeq,
      );
    }
    await ChatSession.findOneAndUpdate({ _id: sessionId }, { $set: { harnessLedger: next } }, { session: tx });
    return result;
  }

  private session(run: HarnessRun, tx: ClientSession) {
    return ChatSession.findOne({
      _id: run.sessionId,
      orgId: run.orgId,
      ...(run.sessionRole === 'collaborator' ? {} : { userId: run.actorId }),
      sessionType: 'chat',
      harnessVersion: 'durable-v1',
      isDeleted: false,
    }).session(tx) as unknown as Promise<IChatSessionDocument | null>;
  }

  async appendUserTurn(sessionId: string, turn: UserTurn, tx: ClientSession): Promise<void> {
    const message = {
      ...buildUserQueryMessage(turn.query, undefined, turn.chatMode, turn.attachments as never),
      ...(turn.senderId !== undefined ? { senderId: new Types.ObjectId(turn.senderId) } : {}),
    };
    await this.accounted(sessionId, tx, () => appendMessages(sessionId, ORG, [message], tx));
  }

  async saveRunTranscript(run: HarnessRun, transcript: RunTranscript, tx: ClientSession): Promise<boolean> {
    return this.accounted(run.sessionId, tx, async () => {
      const conversation = await this.session(run, tx);
      if (conversation === null) return false;
      if (transcript.kind === 'failure') {
        await markConversationFailed(
          conversation,
          transcript.message,
          tx,
          transcript.code,
          undefined,
          undefined,
          transcript.parts as IMessagePart[] | undefined,
          runFailureFields(run.runId, transcript),
        );
        return true;
      }
      const questions = transcript.questions.map(
        (question) =>
          ({
            messageType: 'tool_call',
            content: '',
            tools: [{ toolName: 'ask_user_question', toolResult: question.toolData }],
            createdAt: new Date(),
            updatedAt: new Date(),
          }) as unknown as IMessage,
      );
      if (questions.length > 0) await appendMessages(run.sessionId, run.orgId, questions, tx);
      const reread = (await this.session(run, tx))!;
      await saveCompleteConversation(
        reread,
        transcript.completion as unknown as IAIResponse,
        run.orgId,
        tx,
        run.input?.model as IAIModel | undefined,
      );
      await ChatSessionMessage.findOneAndUpdate(
        // LC2FIX: the answer this transaction wrote, an error answer included.
        { sessionId: run.sessionId, messageType: { $in: ['bot_response', 'error'] } },
        {
          $set: {
            runId: run.runId,
            ...(run.sessionRole === 'collaborator' ? { senderId: new Types.ObjectId(run.actorId) } : {}),
            ...(transcript.label !== undefined ? { label: transcript.label } : {}),
          },
        },
        { sort: { seq: -1 }, session: tx },
      );
      return true;
    });
  }
}

const answer = (text: string, extra: Record<string, unknown> = {}): RunTranscript => ({
  kind: 'answer',
  completion: {
    answer: text,
    citations: [{ content: `source of ${text}`, chunkIndex: 2, citationType: 'vectordb|document', metadata: { recordId: 'r-1', origin: 'UPLOAD', recordName: 'Handbook', mimeType: 'application/pdf' } }],
    confidence: 'High',
    followUpQuestions: [{ question: 'And then?', confidence: 'High', reasoning: 'more' }],
    parts: [{ type: 'text', content: text }],
    referenceData: [{ name: 'PROJ-1', id: '1', app: 'jira', metadata: { key: 'PROJ-1' } }],
    ...extra,
  },
  questions: [],
  label: { complete: true, sources: [{ kind: 'attachment', recordId: 'r-9' }] } as never,
});

/**
 * What a stored document says less what two writes of it never share (ids, clocks): a row's text version
 * (`labelVer`) is minted at its write, so it is compared as there; the ledger's digest, which covers the rows' ids and
 * versions, is checked against each session's own rows (`ownLedger`).
 */
const comparable = (value: unknown, key?: string): unknown => {
  if (value instanceof Types.ObjectId) return 'id';
  if (value instanceof Date) return 'date';
  if (key === 'labelVer' && typeof value === 'string' && Types.ObjectId.isValid(value)) return 'version';
  if (key === 'harnessLedger' && value !== null && typeof value === 'object') {
    return comparable({ ...value, digest: 'digest' });
  }
  if (Array.isArray(value)) return value.map((inner) => comparable(inner));
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([field]) => !['_id', 'lastActivityAt', 'timestamp', 'createdAt', 'updatedAt'].includes(field))
        .map(([field, inner]) => [field, comparable(inner, field)]),
    );
  }
  return value;
};

/** The ledger the session's stored rows give, read as the next import reads them (the history legacy sends). */
const ownLedger = async (sessionId: string, seq: number): Promise<HistoryLedger> => {
  const rows = (await ChatSessionMessage.find({ sessionId }).sort({ seq: 1 }).lean()) as unknown as Array<
    IMessage & { seq: number }
  >;
  return extendLedger(
    EMPTY_LEDGER,
    rows.map((message) => ({
      seq: message.seq,
      turns: JSON.parse(JSON.stringify(sealedHistoryTurns(message))) as Array<Record<string, unknown>>,
    })),
    seq,
  );
};

const storedState = async (sessionId: string) => {
  const session = await ChatSession.findById(sessionId).select('+nextSeq +harnessLedger +sessionType +harnessVersion').lean();
  const messages = await ChatSessionMessage.find({ sessionId }).select('+label').sort({ seq: 1 }).lean();
  const citations = await Citation.find({
    _id: { $in: messages.flatMap((message) => (message.citations ?? []).map((citation) => citation.citationId)) },
  })
    .sort({ content: 1 })
    .lean();
  const ledger = (session as { harnessLedger?: HistoryLedger } | null)?.harnessLedger;
  expect(ledger, 'the session keeps its ledger').to.not.equal(undefined);
  expect(ledger, 'the ledger accounts for the stored rows').to.deep.equal(await ownLedger(sessionId, ledger!.seq));
  return comparable({ session, messages, citations });
};

describe('PERFE2: the lean conversation writer stores what the legacy writer stored', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    await Citation.deleteMany({});
  });

  const newSession = async (): Promise<string> => {
    const sessionId = new Types.ObjectId().toString();
    await inMongoTransaction((tx) =>
      new HarnessChatSessionRepository().createDurableSession(
        { orgId: ORG, actorId: OWNER, sessionId, title: 'A question', model: MODEL as never, now: 1 },
        tx,
      ),
    );
    return sessionId;
  };
  let runs = 0;
  const runOf = (sessionId: string, actorId = OWNER): HarnessRun =>
    ({
      runId: `run-${(runs += 1)}`,
      orgId: ORG,
      sessionId,
      actorId,
      ...(actorId === COLLABORATOR ? { sessionRole: 'collaborator' } : {}),
      input: { model: MODEL },
    }) as unknown as HarnessRun;

  /** Each step in a transaction of its own, as admission and the ending commit run them. */
  const conversation = async (
    write: {
      turn: (sessionId: string, turn: UserTurn, tx: ClientSession) => Promise<unknown>;
      transcript: (run: HarnessRun, transcript: RunTranscript, tx: ClientSession) => Promise<boolean>;
    },
    batched: boolean,
  ): Promise<string> => {
    const sessionId = await newSession();
    runs = 0;
    const step =(work: (tx: ClientSession) => Promise<unknown>) => inMongoTransaction(work, { batchWrites: batched });
    await step((tx) => write.turn(sessionId, { query: 'What is PTO?', chatMode: 'quick', attachments: [] }, tx));
    await step((tx) => write.transcript(runOf(sessionId), answer('Twenty days.'), tx));
    await step((tx) =>
      write.turn(sessionId, { query: 'And sick days?', chatMode: 'quick', attachments: [], senderId: COLLABORATOR }, tx),
    );
    await step((tx) =>
      write.transcript(
        runOf(sessionId, COLLABORATOR),
        { ...answer('Ten.'), questions: [{ toolData: { questions: [{ question: 'Which country?' }] } }] } as RunTranscript,
        tx,
      ),
    );
    await step((tx) => write.turn(sessionId, { query: 'Stop.', chatMode: 'quick', attachments: [] }, tx));
    await step((tx) => write.transcript(runOf(sessionId), answer('Stopped here.', { status: 'stopped' }), tx));
    await step((tx) => write.turn(sessionId, { query: 'Again', chatMode: 'quick', attachments: [] }, tx));
    await step((tx) =>
      write.transcript(
        runOf(sessionId),
        {
          kind: 'failure',
          code: 'quarantined',
          message: 'It kept failing.',
          parts: [{ type: 'user_input', content: 'keep me', messageSeq: 1, consumed: false }],
        },
        tx,
      ),
    );
    return sessionId;
  };

  const legacy = new LegacyWriter();
  const lean = new HarnessConversationWriter();
  const leanWrite = {
    turn: (sessionId: string, turn: UserTurn, tx: ClientSession) => lean.appendUserTurn({ orgId: ORG, sessionId }, turn, tx),
    transcript: (run: HarnessRun, transcript: RunTranscript, tx: ClientSession) => lean.saveRunTranscript(run, transcript, tx),
  };
  const legacyWrite = {
    turn: (sessionId: string, turn: UserTurn, tx: ClientSession) => legacy.appendUserTurn(sessionId, turn, tx),
    transcript: (run: HarnessRun, transcript: RunTranscript, tx: ClientSession) => legacy.saveRunTranscript(run, transcript, tx),
  };

  for (const batched of [true, false]) {
    it(`PERFE2-6: the same session, messages, citations and ledger (${batched ? 'inserts batched' : 'MongoDB before 8'})`, async () => {
      const before = await storedState(await conversation(legacyWrite, false));
      const after = await storedState(await conversation(leanWrite, batched));
      expect(after).to.deep.equal(before);
      const ledger = (before as { session: { harnessLedger: HistoryLedger } }).session.harnessLedger;
      expect(ledger.diverged, 'the harness wrote every message: nothing to re-import').to.equal(undefined);
    });
  }

  it('PERFE2-7: the next import reads the history the ledger accounts for', async () => {
    const sessionId = await conversation(leanWrite, true);
    const history = await inMongoTransaction((tx) => new HarnessHistorySource().read({ orgId: ORG, sessionId }, tx));
    const recomputed = extendLedger(
      { seq: 0, digest: (await newLedger()).digest },
      history.messages,
      history.lastSeq,
    );
    expect(history.ledger).to.deep.equal(recomputed);
  });

  const newLedger = async (): Promise<HistoryLedger> => {
    const sessionId = await newSession();
    const row = await ChatSession.findById(sessionId).select('+harnessLedger').lean();
    return row!.harnessLedger as HistoryLedger;
  };

  it('PERFE2-B1: an answer classified as a failure leaves the answer before it as it was', async () => {
    const sessionId = await newSession();
    runs = 0;
    await inMongoTransaction((tx) => lean.saveRunTranscript(runOf(sessionId), answer('First.'), tx));
    await inMongoTransaction((tx) =>
      lean.saveRunTranscript(runOf(sessionId), answer('Model error.', { answerMatchType: 'Error', errorCode: 'model_failed' }), tx),
    );
    const messages = await ChatSessionMessage.find({ sessionId }).sort({ seq: 1 }).lean();
    // The legacy writer stamped the newest bot_response, which for an error answer was the one before it; LC2FIX stamps
    // the error answer itself.
    expect(messages.map((message) => [message.messageType, message.runId ?? null])).to.deep.equal([
      ['bot_response', 'run-1'],
      ['error', 'run-2'],
    ]);
  });

  it('PERFE2-8: an answer is one read and one write of the session; the write, its messages and citations go with the batch (PERF-E3)', async () => {
    const sessionId = await newSession();
    await inMongoTransaction((tx) =>
      lean.appendUserTurn({ orgId: ORG, sessionId }, { query: 'q', chatMode: 'quick', attachments: [] }, tx),
    );
    const commands: string[] = [];
    const count = (event: CommandStartedEvent): void => {
      const target = event.command[event.commandName] as unknown;
      commands.push(`${event.commandName} ${typeof target === 'string' ? target : ''}`.trim());
    };
    const client = mongoose.connection.getClient();
    client.on('commandStarted', count);
    try {
      await inMongoTransaction((tx) => lean.saveRunTranscript(runOf(sessionId), answer('A.'), tx), { batchWrites: true });
    } finally {
      client.off('commandStarted', count);
    }
    expect(commands).to.deep.equal(['find chatSessions', 'bulkWrite', 'commitTransaction']);
  });
});
