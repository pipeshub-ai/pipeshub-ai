import { redactTokens } from '../domain/token-redaction';
import { HarnessRun } from '../domain/execution';
import { ChatSessionRepository } from '../ports/chat-session-repository';
import { ExecutionStore } from '../ports/execution-store';
import { QuestionView } from '../projection/timeline';
import { RunTimelineService } from './run-timeline-service';

/** The gateway's own bounds for `ask_user` (harness_core/gateway/asking.py). */
const MAX_QUESTION_CHARS = 2_000;
const MAX_OPTION_CHARS = 200;
const MAX_OPTIONS = 7;

export interface QuestionInboxQuery {
  orgId: string;
  actorId: string;
  agentKey?: string;
  limit: number;
}

/**
 * A question a run asked the viewer and nobody has answered (G-13). The
 * answer is the person's next message in that conversation, the same as from
 * the chat's card: it starts the session's next run, which carries on.
 */
export interface InboxQuestion {
  runId: string;
  sessionId: string;
  agentKey?: string;
  runUpdatedAt: number;
  callId: string;
  question: string;
  options: string[];
  multiSelect: boolean;
  /** Further questions the same card asks; the conversation shows them all. */
  moreQuestions: number;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const clip = (text: string, max: number): string =>
  Array.from(text).length <= max
    ? text
    : Array.from(text).slice(0, max).join('');

/** The card's first question, as `ask_user_question` and the gateway's `ask_user` write it; null: nothing to answer. */
function asked(
  view: QuestionView,
): Omit<
  InboxQuestion,
  'runId' | 'sessionId' | 'agentKey' | 'runUpdatedAt'
> | null {
  if (view.status !== 'success') return null;
  const data = view.toolData;
  if (typeof data === 'string') {
    const question = data.trim();
    return question.length === 0
      ? null
      : {
          callId: view.callId,
          question: clip(redactTokens(question), MAX_QUESTION_CHARS),
          options: [],
          multiSelect: false,
          moreQuestions: 0,
        };
  }
  if (!isObject(data) || !Array.isArray(data['questions'])) return null;
  const questions = (data['questions'] as unknown[]).filter(isObject);
  const first = questions[0];
  const text =
    typeof first?.['question'] === 'string' ? first['question'].trim() : '';
  if (first === undefined || text.length === 0) return null;
  const options = (
    Array.isArray(first['options']) ? (first['options'] as unknown[]) : []
  )
    .map((option) =>
      isObject(option) && typeof option['label'] === 'string'
        ? option['label']
        : typeof option === 'string'
          ? option
          : '',
    )
    .filter((label) => label.trim().length > 0)
    .slice(0, MAX_OPTIONS)
    .map((label) => clip(redactTokens(label), MAX_OPTION_CHARS));
  return {
    callId: view.callId,
    question: clip(redactTokens(text), MAX_QUESTION_CHARS),
    options,
    multiSelect: first['multiSelect'] === true,
    moreQuestions: questions.length - 1,
  };
}

/**
 * The questions waiting on the viewer across their conversations (G-13): a
 * run asks only the person it acts for, so only their own runs are read, and
 * only in a conversation they can still open.
 */
export class QuestionInboxService {
  constructor(
    private readonly runs: Pick<ExecutionStore, 'unansweredQuestionRuns'>,
    private readonly sessions: Pick<ChatSessionRepository, 'findStreamAccess'>,
    private readonly timelines: Pick<RunTimelineService, 'questions'>,
  ) {}

  async pending(query: QuestionInboxQuery): Promise<InboxQuestion[]> {
    const runs = await this.runs.unansweredQuestionRuns(query.orgId, {
      actorId: query.actorId,
      limit: query.limit,
      ...(query.agentKey !== undefined ? { agentKey: query.agentKey } : {}),
    });
    const found = await Promise.all(
      runs.map((run) => this.question(query, run)),
    );
    return found.filter((item): item is InboxQuestion => item !== null);
  }

  private async question(
    query: QuestionInboxQuery,
    run: HarnessRun,
  ): Promise<InboxQuestion | null> {
    if (run.actorId !== query.actorId || run.orgId !== query.orgId) return null;
    const agentKey =
      run.agentKey !== undefined && run.agentKey.length > 0
        ? run.agentKey
        : undefined;
    const access = await this.sessions.findStreamAccess({
      orgId: run.orgId,
      actorId: query.actorId,
      sessionId: run.sessionId,
      ...(agentKey !== undefined ? { agentKey } : {}),
    });
    if (access === null) return null;
    const views = await this.timelines.questions(run);
    const latest = views[views.length - 1];
    const card = latest === undefined ? null : asked(latest);
    if (card === null) return null;
    return {
      runId: run.runId,
      sessionId: run.sessionId,
      ...(agentKey !== undefined ? { agentKey } : {}),
      runUpdatedAt: run.updatedAt,
      ...card,
    };
  }
}
