import {
  getConversationRepository,
  type HarnessSessionRow,
  type NewMessage,
  type RunStateChange,
} from '../../conversation';
import type { Tx } from '../../uow';
import type {
  IAIModel,
  IAIResponse,
  IMessage,
  IMessagePart,
} from '../../../../modules/enterprise_search/types/conversation.interfaces';
import {
  buildAIFailureResponseMessage,
  buildAIResponseMessage,
  buildSessionError,
  buildUserQueryMessage,
  classifiedFailureChange,
  REGENERATE_TAIL_MESSAGES,
} from '../../../../modules/enterprise_search/utils/utils';
import { CONVERSATION_STATUS } from '../../../../modules/enterprise_search/constants/constants';
import { NotFoundError } from '../../../errors/http.errors';
import type { HarnessRun } from '../../../../modules/agent-harness/domain/execution';
import type { TurnLabel } from '../../../../modules/agent-harness/domain/turn-labels';
import type { AccountedConversation } from '../../../../modules/agent-harness/domain/session-import';
import type {
  ConversationRef,
  ConversationStatus,
  ConversationWriter,
  RunTranscript,
  UserTurn,
} from '../../../../modules/agent-harness/ports/conversation-writer';
import { appendAccountedPg, lockAccounted } from '../history-source/pg';
import { hasValidIds, sessionIs } from '../chat-session-repository/pg';
import { runFailureFields } from './mongo';

type DurableRun = Pick<
  HarnessRun,
  'orgId' | 'sessionId' | 'actorId' | 'sessionRole' | 'agentKey'
>;

/** The run's durable session, as its actor may write it; a collaborator's run writes the session admission bound it to (SS2). */
const durableSession =
  (run: DurableRun) =>
  (row: HarnessSessionRow): boolean =>
    sessionIs.live(row) &&
    sessionIs.durable(row) &&
    sessionIs.ofKind(row, run.agentKey, (key) => key !== undefined) &&
    (run.sessionRole === 'collaborator' || sessionIs.owner(row, run.actorId));

const refOf = (
  run: Pick<HarnessRun, 'orgId' | 'sessionId'>,
): ConversationRef => ({
  orgId: run.orgId,
  sessionId: run.sessionId,
});

/**
 * The durable path's conversation writer on PostgreSQL: what the MongoDB writer stores (the legacy chat API's shape:
 * the messages, their citations, the session's status and model, the history ledger), through the conversation
 * repository, in the caller's unit of work. The session row is locked first, so its checks hold to the commit.
 */
export class PgHarnessConversationWriter implements ConversationWriter<Tx> {
  async appendUserTurn(
    ref: ConversationRef,
    turn: UserTurn,
    tx: Tx,
    accounted?: AccountedConversation,
  ): Promise<string> {
    const message: NewMessage = {
      ...buildUserQueryMessage(
        turn.query,
        turn.appliedFilters,
        turn.chatMode,
        turn.attachments as unknown as Parameters<
          typeof buildUserQueryMessage
        >[3],
        turn.command,
      ),
      ...(turn.senderId !== undefined ? { senderId: turn.senderId } : {}),
    };
    const known = accounted ?? (await lockAccounted(ref, tx));
    if (known === null) {
      throw new NotFoundError(
        'Chat session not found while allocating message sequence',
      );
    }
    const [stored] = await appendAccountedPg(ref, tx, known, [message]);
    if (stored === undefined) {
      throw new NotFoundError(
        'Chat session not found while allocating message sequence',
      );
    }
    return stored._id;
  }

  async hasAnswer(
    ref: ConversationRef,
    answerId: string,
    tx: Tx,
  ): Promise<boolean> {
    if (!hasValidIds(answerId)) return false;
    const found = await getConversationRepository().findMessage(
      ref.sessionId,
      ref.orgId,
      answerId,
      tx,
    );
    return found?.messageType === 'bot_response';
  }

  async retractLastAnswer(
    ref: ConversationRef,
    answerId: string,
    tx: Tx,
  ): Promise<UserTurn | null> {
    if (!hasValidIds(answerId)) return null;
    const repo = getConversationRepository();
    const tail = (
      await repo.listMessages(
        ref.sessionId,
        { orgId: ref.orgId, limit: REGENERATE_TAIL_MESSAGES, order: 'desc' },
        tx,
      )
    ).map((row) => row.dto);
    const answerAt = tail.findIndex((m) => m.messageType === 'bot_response');
    if (answerAt < 0 || tail[answerAt]?._id !== answerId) return null;
    const question = tail
      .slice(answerAt + 1)
      .find((m) => m.messageType === 'user_query');
    if (question === undefined) return null;
    // The tail is newest first, so every row from the question on is in it.
    await repo.deleteMessages(
      tail.filter((m) => m.seq >= question.seq).map((m) => m._id),
      ref.orgId,
      tx,
      ref.sessionId,
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
    const row = await getConversationRepository().findHarnessSession(
      run.sessionId,
      run.orgId,
    );
    return row !== null && durableSession(run)(row);
  }

  async saveRunTranscript(
    run: HarnessRun,
    transcript: RunTranscript,
    tx: Tx,
  ): Promise<boolean> {
    if (transcript.kind === 'failure') {
      const accounted = await lockAccounted(
        refOf(run),
        tx,
        durableSession(run),
      );
      if (accounted === null) return false;
      await appendAccountedPg(
        refOf(run),
        tx,
        accounted,
        [
          buildAIFailureResponseMessage(
            transcript.message,
            transcript.parts as IMessagePart[] | undefined,
            runFailureFields(run.runId, transcript),
          ),
        ],
        failed(transcript.message, transcript.code),
      );
      return true;
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

  saveCompletedConversation(
    run: HarnessRun,
    completion: Record<string, unknown>,
    tx: Tx,
  ): Promise<boolean> {
    return this.saveAnswer(run, completion, tx, []);
  }

  /** The answer with its citations (saved first: the answer refers to them by id), the session's status and model. */
  private async saveAnswer(
    run: HarnessRun,
    completion: Record<string, unknown>,
    tx: Tx,
    questions: IMessage[],
    label?: TurnLabel,
  ): Promise<boolean> {
    const accounted = await lockAccounted(refOf(run), tx, durableSession(run));
    if (accounted === null) return false;
    const data = completion as unknown as IAIResponse;
    // A completion is the engine's record, not a checked IAIResponse: it may carry no citations.
    const recorded: { citations?: IAIResponse['citations'] } = data;
    const modelInfo = run.input?.model as IAIModel | undefined;
    const citations = await getConversationRepository().createCitations(
      (recorded.citations ?? []).map((citation) => ({
        content: citation.content,
        chunkIndex: citation.chunkIndex,
        citationType: citation.citationType,
        metadata: { ...citation.metadata, orgId: run.orgId },
      })),
      tx,
    );
    const answer = buildAIResponseMessage(
      { data, statusCode: 200 },
      citations,
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
          ? { senderId: run.actorId }
          : {}),
        ...(label !== undefined ? { label } : {}),
      });
    }
    const change = classifiedFailureChange(data);
    await appendAccountedPg(
      refOf(run),
      tx,
      accounted,
      [...questions, answer] as Array<IMessage | NewMessage>,
      {
        ...(modelInfo !== undefined ? { modelInfo } : {}),
        lastActivityAt: Date.now(),
        status: change.status,
        ...(change.failReason !== undefined
          ? { failReason: change.failReason }
          : {}),
        ...(change.appendError !== undefined
          ? { appendError: change.appendError }
          : {}),
      },
    );
    return true;
  }

  async setConversationStatus(
    run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'actorId' | 'sessionRole'>,
    status: ConversationStatus,
    now: number,
    tx: Tx,
  ): Promise<void> {
    const repo = getConversationRepository();
    const row = await repo.findHarnessSession(
      run.sessionId,
      run.orgId,
      { forUpdate: true },
      tx,
    );
    if (
      row === null ||
      !(run.sessionRole === 'collaborator' || sessionIs.owner(row, run.actorId))
    ) {
      return;
    }
    await repo.setHarnessFields(
      run.sessionId,
      run.orgId,
      { status, lastActivityAt: now },
      tx,
    );
  }
}

/** What marking a conversation failed sets on it (markConversationFailed). */
const failed = (reason: string, code: string): RunStateChange => ({
  status: CONVERSATION_STATUS.FAILED,
  failReason: reason,
  lastActivityAt: Date.now(),
  appendError: buildSessionError(reason, code),
});
