import { Types } from 'mongoose';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import { ResumeNotAllowedError } from '../domain/errors';
import { ConversationAccessFields } from '../domain/types';
import { TurnScope } from './turn-preconditions';

// What the browser sends today to answer an `ask_user_question` card. Python matches it after
// `str.lstrip()`, whose whitespace also covers \x1c-\x1f and \x85 (not in JS `\s`).
// eslint-disable-next-line no-control-regex -- matching those characters is the point
const SELECTIONS_TEXT = /^[\s\x1c-\x1f\x85]*User selections:/u;

/** True when the AI backend would read `query` as the answer to a card. */
export const isSelectionsText = (query: unknown): boolean =>
  typeof query === 'string' && SELECTIONS_TEXT.test(query);

export interface ResumeBody {
  query?: unknown;
  resume?: { toolCallMessageId: string };
}

interface CardRow {
  _id: Types.ObjectId;
  seq: number;
  requestedBy?: Types.ObjectId;
}

type SharingFields = Pick<
  ConversationAccessFields,
  'sharedWith' | 'projectVisibility'
>;

/** A chat other people can write into: any share row, or visible to its project. */
export const isCollaborative = (session: SharingFields): boolean =>
  (session.sharedWith?.length ?? 0) > 0 ||
  session.projectVisibility === 'project';

const ASK_TOOL = { 'tools.toolName': { $regex: 'ask_user_question' } };

/**
 * The card a resume answers: the newest `ask_user_question` row, and only while no `user_query`
 * came after it (the question is still unanswered). A regenerated answer carries its card on the
 * `bot_response` itself, with no `tool_call` row beside it.
 */
async function pendingCard(scope: TurnScope): Promise<CardRow | undefined> {
  const [card] = await ChatSessionMessage.find({
    sessionId: scope.sessionId,
    orgId: scope.orgId,
    messageType: { $in: ['tool_call', 'bot_response'] },
    ...ASK_TOOL,
  })
    .sort({ seq: -1 })
    .limit(1)
    .select('_id seq requestedBy')
    .lean<CardRow[]>();
  if (!card) return undefined;
  const answered = await ChatSessionMessage.countDocuments({
    sessionId: scope.sessionId,
    orgId: scope.orgId,
    messageType: 'user_query',
    seq: { $gt: card.seq },
  });
  return answered > 0 ? undefined : card;
}

/** Legacy cards carry no `requestedBy`: the question was put by whoever wrote the turn's `user_query`, else the owner. */
async function askedOf(scope: TurnScope, card: CardRow): Promise<string> {
  if (card.requestedBy) return card.requestedBy.toString();
  const [question] = await ChatSessionMessage.find({
    sessionId: scope.sessionId,
    orgId: scope.orgId,
    messageType: 'user_query',
    seq: { $lt: card.seq },
  })
    .sort({ seq: -1 })
    .limit(1)
    .select('authorUserId')
    .lean<Array<{ authorUserId?: Types.ObjectId }>>();
  return question?.authorUserId?.toString() ?? scope.ownerId;
}

/**
 * DF-8: only the person a question was put to may answer it. Applies to a body `resume`, and to the
 * text `User selections:` in a collaborative chat (an interim until the AI backend stops reading the
 * prefix); a solo chat's text is left alone. Returns the card the request is bound to, or undefined
 * when it does not answer one. Runs before the lease, so a denial takes none and makes no AI call.
 */
export async function assertResumeAllowed(
  scope: TurnScope,
  session: SharingFields,
  body: ResumeBody,
): Promise<string | undefined> {
  const named = body.resume?.toolCallMessageId;
  const byText =
    named === undefined &&
    isSelectionsText(body.query) &&
    isCollaborative(session);
  if (named === undefined && !byText) return undefined;

  const card = await pendingCard(scope);
  if (
    !card ||
    (named !== undefined && card._id.toString() !== named) ||
    (await askedOf(scope, card)) !== scope.callerId
  ) {
    throw new ResumeNotAllowedError();
  }
  return card._id.toString();
}
