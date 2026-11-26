import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession, QueryFilter, Types } from 'mongoose';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../../modules/enterprise_search/schema/citation.schema';
import {
  IAIModel,
  IAIResponse,
  IChatSessionDocument,
  IMessage,
  IMessagePart,
} from '../../../../modules/enterprise_search/types/conversation.interfaces';
import {
  buildAIFailureResponseMessage,
  buildAIResponseMessage,
  buildSessionError,
  buildUserQueryMessage,
  classifiedFailureChange,
  getMessages,
  REGENERATE_TAIL_MESSAGES,
  RunFailureFields,
} from '../../../../modules/enterprise_search/utils/utils';
import { MongoTx } from '../../mongo';
import type {
  CitationDto,
  NewMessage,
  NewSessionError,
} from '../../conversation/types';
import { CONVERSATION_STATUS } from '../../../../modules/enterprise_search/constants/constants';
import { NotFoundError } from '../../../errors/http.errors';
import { HarnessRun } from '../../../../modules/agent-harness/domain/execution';
import type { TurnLabel } from '../../../../modules/agent-harness/domain/turn-labels';
import type { AccountedConversation } from '../../../../modules/agent-harness/domain/session-import';
import { transactionWrites } from '../transaction-writes/mongo';
import { appendAccounted, SessionWrite } from '../history-source/mongo';
import { failedOutcome } from '../../../../modules/agent-harness/projection/outcome-view';
import {
  ConversationRef,
  ConversationStatus,
  ConversationWriter,
  RunTranscript,
  UserTurn,
} from '../../../../modules/agent-harness/ports/conversation-writer';

type FailureTranscript = Extract<RunTranscript, { kind: 'failure' }>;

/** The one mapping from a run's failure to what its error message stores, for every writer of a durable failure. */
export const runFailureFields = (
  runId: string,
  transcript: FailureTranscript,
): RunFailureFields => ({
  runId,
  errorCode: transcript.code,
  ...(transcript.servers !== undefined
    ? { errorServers: transcript.servers }
    : {}),
  ...(transcript.stop !== undefined ? { errorStop: transcript.stop } : {}),
  runOutcome: failedOutcome(transcript),
});

export class HarnessConversationWriter implements ConversationWriter<Tx> {
  async appendUserTurn(
    ref: ConversationRef,
    turn: UserTurn,
    txHandle: Tx | ClientSession,
    accounted?: AccountedConversation,
  ): Promise<string> {
    const tx = asMongoSession(txHandle);
    const messageId = new Types.ObjectId();
    const message = {
      ...buildUserQueryMessage(
        turn.query,
        turn.appliedFilters,
        turn.chatMode,
        turn.attachments as unknown as Parameters<
          typeof buildUserQueryMessage
        >[3],
        turn.command,
      ),
      _id: messageId,
      ...(turn.senderId !== undefined
        ? { senderId: new Types.ObjectId(turn.senderId) }
        : {}),
    };
    const appended = await appendAccounted(
      { _id: ref.sessionId },
      tx,
      [message],
      {},
      accounted !== undefined ? { ref, accounted } : undefined,
    );
    if (appended === null) {
      throw new NotFoundError(
        'Chat session not found while allocating message sequence',
      );
    }
    return messageId.toString();
  }

  async hasAnswer(
    ref: ConversationRef,
    answerId: string,
    txHandle: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    if (!Types.ObjectId.isValid(answerId)) return false;
    const found = await ChatSessionMessage.exists({
      _id: answerId,
      sessionId: ref.sessionId,
      orgId: ref.orgId,
      messageType: 'bot_response',
    }).session(tx);
    return found !== null;
  }

  async retractLastAnswer(
    ref: ConversationRef,
    answerId: string,
    txHandle: Tx | ClientSession,
  ): Promise<UserTurn | null> {
    const tx = asMongoSession(txHandle);
    if (!Types.ObjectId.isValid(answerId)) return null;
    const tail = (await getMessages(
      ref.sessionId,
      { orgId: ref.orgId, limit: REGENERATE_TAIL_MESSAGES, sort: -1 },
      new MongoTx(tx),
    )) as Array<IMessage & { _id: Types.ObjectId; seq: number }>;
    const answerAt = tail.findIndex((m) => m.messageType === 'bot_response');
    if (answerAt < 0 || String(tail[answerAt]?._id) !== answerId) return null;
    const question = tail
      .slice(answerAt + 1)
      .find((m) => m.messageType === 'user_query');
    if (question === undefined) return null;
    await ChatSessionMessage.deleteMany(
      {
        sessionId: ref.sessionId,
        orgId: ref.orgId,
        seq: { $gte: question.seq },
      },
      { session: tx },
    );
    return {
      query: question.content,
      ...(question.appliedFilters !== undefined
        ? {
            appliedFilters: question.appliedFilters as unknown as Record<
              string,
              unknown
            >,
          }
        : {}),
      chatMode: question.modelInfo?.chatMode ?? '',
      attachments: (question.attachments ?? []) as unknown as Array<
        Record<string, unknown>
      >,
      ...(question.command ? { command: question.command } : {}),
    };
  }

  async runSessionExists(run: HarnessRun): Promise<boolean> {
    return (await ChatSession.exists(durableSession(run))) !== null;
  }

  async saveRunTranscript(
    run: HarnessRun,
    transcript: RunTranscript,
    txHandle: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    if (transcript.kind === 'failure') {
      return this.append(
        run,
        tx,
        [
          buildAIFailureResponseMessage(
            transcript.message,
            transcript.parts as IMessagePart[] | undefined,
            runFailureFields(run.runId, transcript),
          ),
        ],
        failed(transcript.message, transcript.code),
      );
    }
    // The legacy stream persisted each question card before the answer.
    const questions = transcript.questions.map(
      (question) =>
        ({
          messageType: 'tool_call',
          content: '',
          tools: [
            { toolName: 'ask_user_question', toolResult: question.toolData },
          ],
          createdAt: new Date(),
          updatedAt: new Date(),
        }) as unknown as IMessage,
    );
    return this.saveAnswer(
      run,
      transcript.completion,
      tx,
      questions,
      transcript.label,
    );
  }

  async saveCompletedConversation(
    run: HarnessRun,
    completion: Record<string, unknown>,
    txHandle: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    return this.saveAnswer(run, completion, tx, []);
  }

  /**
   * What the legacy stream's save stored for an answer (its citations, the
   * answer, the session's status and model), in one read and one write of
   * the session (PERF-E2); the answer carries its run, its collaborator and
   * its label from the start.
   */
  private async saveAnswer(
    run: HarnessRun,
    completion: Record<string, unknown>,
    txHandle: Tx | ClientSession,
    questions: IMessage[],
    label?: TurnLabel,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    const data = completion as unknown as IAIResponse;
    // A completion is the engine's record, not a checked IAIResponse: it may carry no citations.
    const recorded: { citations?: IAIResponse['citations'] } = data;
    const modelInfo = run.input?.model as IAIModel | undefined;
    const citations = (recorded.citations ?? []).map(
      (citation) =>
        new Citation({
          content: citation.content,
          chunkIndex: citation.chunkIndex,
          citationType: citation.citationType,
          metadata: { ...citation.metadata, orgId: run.orgId },
        }),
    );
    const answer = buildAIResponseMessage(
      { data, statusCode: 200 },
      // The answer refers to its citations by id; the documents are saved below, in the same transaction.
      citations.map((citation) => citationDto(citation)),
      modelInfo,
    );
    // An answer classified as a failure is stored as an `error` answer and carries its run too (LC2FIX).
    if (
      answer.messageType === 'bot_response' ||
      answer.messageType === 'error'
    ) {
      Object.assign(answer, {
        runId: run.runId,
        // The answer is the collaborator's turn too: history read for anyone else stubs it.
        ...(run.sessionRole === 'collaborator'
          ? { senderId: new Types.ObjectId(run.actorId) }
          : {}),
        ...(label !== undefined ? { label } : {}),
      });
    }
    const change = classifiedFailureChange(data);
    const appended = await this.append(run, tx, [...questions, answer], {
      $set: {
        ...modelFields(modelInfo),
        lastActivityAt: Date.now(),
        status: change.status,
        ...(change.failReason !== undefined
          ? { failReason: change.failReason }
          : {}),
      },
      ...errorPush(change.appendError),
    });
    if (appended && citations.length > 0) {
      // Alone (MongoDB before 8), each is saved as the legacy save saved it.
      await transactionWrites.insert(tx, Citation, citations, () =>
        Promise.all(
          citations.map((citation) => citation.save({ session: tx })),
        ),
      );
    }
    return appended;
  }

  /** False when the run's durable session no longer exists for its actor. */
  private async append(
    run: HarnessRun,
    txHandle: Tx | ClientSession,
    messages: Array<IMessage | NewMessage>,
    write: SessionWrite,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    return (
      (await appendAccounted(durableSession(run), tx, messages, write)) !== null
    );
  }

  async setConversationStatus(
    run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'actorId' | 'sessionRole'>,
    status: ConversationStatus,
    now: number,
    txHandle: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    await ChatSession.updateOne(
      {
        _id: run.sessionId,
        orgId: run.orgId,
        ...(run.sessionRole === 'collaborator' ? {} : { userId: run.actorId }),
      },
      { $set: { status, lastActivityAt: now } },
      { session: tx },
    );
  }
}

/** The run's durable session, as its actor may write it. */
const durableSession = (
  run: HarnessRun,
): QueryFilter<IChatSessionDocument> => ({
  _id: run.sessionId,
  orgId: run.orgId,
  // A collaborator's run writes the session admission bound it to (SS2).
  ...(run.sessionRole === 'collaborator' ? {} : { userId: run.actorId }),
  ...(run.agentKey !== undefined
    ? { agentKey: run.agentKey, sessionType: 'agent' }
    : { sessionType: 'chat' }),
  harnessVersion: 'durable-v1',
  isDeleted: false,
});

const MODEL_FIELDS: Array<keyof IAIModel> = [
  'modelKey',
  'modelName',
  'modelProvider',
  'chatMode',
  'modelFriendlyName',
  'reasoningEffort',
];

/** The run's model on the session, field by field, as the legacy save sets it. */
const modelFields = (model?: IAIModel): Record<string, unknown> =>
  Object.fromEntries(
    MODEL_FIELDS.flatMap((field) => {
      const value = model?.[field];
      return value == null ? [] : [[`modelInfo.${field}`, value]];
    }),
  );

/** A failure's entry pushed onto the session's `conversationErrors`, stamped as the legacy save stamped it. */
const errorPush = (error: NewSessionError | undefined): SessionWrite =>
  error !== undefined
    ? {
        $push: {
          conversationErrors: { $each: [{ ...error, timestamp: new Date() }] },
        },
      }
    : {};

/** What marking a conversation failed sets on it (markConversationFailed). */
const failed = (reason: string, code: string): SessionWrite => ({
  $set: {
    status: CONVERSATION_STATUS.FAILED,
    failReason: reason,
    lastActivityAt: Date.now(),
  },
  ...errorPush(buildSessionError(reason, code)),
});

const citationDto = (citation: InstanceType<typeof Citation>): CitationDto => ({
  _id: String(citation._id),
  content: citation.content,
  chunkIndex: citation.chunkIndex,
  citationType: citation.citationType,
  metadata: { ...citation.metadata },
});
