import mongoose, { ClientSession } from 'mongoose';
import {
  ConflictError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import { ProjectService } from '../../projects/services/project.service';
import { CONVERSATION_STATUS } from '../constants/constants';
import { FOREIGN_TURN_STUB } from '../../agent-harness/domain/session-audience';
import { getConversationRepository } from '../../../libs/repositories/conversation';
import type {
  ChatSessionDto,
  MessageDto,
  MessageRow,
  NewMessage,
  SessionScope,
} from '../../../libs/repositories/conversation/types';
import type { ConversationRepository } from '../../../libs/repositories/conversation';
import { MongoTx, sessionOf } from '../../../libs/repositories/mongo';
import type { Tx } from '../../../libs/repositories/uow';
import { ChatSession } from '../schema/chat.session.schema';
import {
  ChatSessionMessage,
  NOT_REMOVED,
} from '../schema/chat.session.message.schema';
import {
  IChatSession,
  IChatSessionDocument,
  IMessage,
  IMessagePart,
} from '../types/conversation.interfaces';
import {
  ArtifactCopier,
  ArtifactCopyMap,
  collectArtifactRefs,
  pointMessageAtCopies,
} from './fork-artifacts';
import { appendMessages } from './utils';
import { detachCopiedRuns } from './copied-runs';
import {
  harnessVersionOf,
  pinForkExecutor,
} from '../../../libs/repositories/agent-harness/session-fields';

import {
  idOf as idOfValue,
  rowAuthorOf,
  visibleRows,
  withoutQuote,
  type ReaderRow,
} from '../services/conversation/privacy/visibility';

const logger = Logger.getInstance({ service: 'ForkConversation' });

export const FORK_TITLE_MAX_LENGTH = 200;
export const FORK_TITLE_PREFIX = 'Fork of ';
export const FORK_COPY_BATCH_SIZE = 200;

const MESSAGE_COPY_OMIT = new Set([
  '_id',
  '__v',
  'id',
  'sessionId',
  'orgId',
  'seq',
  'createdAt',
  'updatedAt',
  'feedback',
  'authorUserId',
  // set by a store that projects per row; the copy is the caller's own
  'detailsHidden',
  // A share is for the chat it was made in.
  'consent',
]);

export function buildForkTitle(
  originalTitle?: string | null,
  ordinal = 1,
): string {
  const base = (originalTitle ?? '').trim() || 'Untitled';
  const suffix = ordinal > 1 ? ` (${ordinal})` : '';
  return (
    `${FORK_TITLE_PREFIX}${base}`.slice(
      0,
      FORK_TITLE_MAX_LENGTH - suffix.length,
    ) + suffix
  );
}

/**
 * "Fork of X", then "Fork of X (2)", "(3)" and so on, the first number no
 * other live fork of this chat uses, so two forks of one chat can be told
 * apart in the list. Never fails the fork: without the list it is "Fork of X".
 */
export async function forkTitleFor(
  source: Pick<ChatSessionDto, '_id' | 'title' | 'agentKey'>,
  userId: string,
  orgId: string,
  repo: ConversationRepository = getConversationRepository(),
): Promise<string> {
  try {
    const scope: SessionScope = source.agentKey
      ? { kind: 'agent', agentKey: source.agentKey }
      : { kind: 'chat' };
    const earlier = await repo.listForksOf(scope, source._id, {
      orgId,
      userId,
    });
    const taken = new Set(earlier.map((fork) => fork.title));
    let ordinal = 1;
    while (
      ordinal <= earlier.length &&
      taken.has(buildForkTitle(source.title, ordinal))
    ) {
      ordinal += 1;
    }
    return buildForkTitle(source.title, ordinal);
  } catch (error) {
    logger.warn('Could not number a fork', {
      conversationId: source._id,
      error: error instanceof Error ? error.message : String(error),
    });
    return buildForkTitle(source.title);
  }
}

export function stripMessageForCopy(
  message: Record<string, unknown>,
): IMessage {
  const copy: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(message)) {
    if (MESSAGE_COPY_OMIT.has(key)) {
      continue;
    }
    copy[key] = value;
  }
  return copy as unknown as IMessage;
}

const EXPIRED_APPROVAL_PREVIEW = 'The approval request expired.';

/**
 * A tool call still waiting for approval in the original can only be answered
 * there: Python ties the pending approval to its conversation and ignores an
 * answer sent from another one. In the copy it is settled as expired, the way
 * the original would settle it once the window ran out, so the fork shows a
 * receipt instead of a card that does nothing. Parts are never mutated.
 */
export function expirePendingApprovals(
  parts: IMessagePart[] | undefined,
): IMessagePart[] | undefined {
  if (!Array.isArray(parts)) {
    return parts;
  }
  const settled = parts.map((part) => {
    // Persisted parts are Mixed: an element is not guaranteed to be an object.
    const candidate: unknown = part;
    if (typeof candidate !== 'object' || candidate === null) {
      return part;
    }
    const nested = expirePendingApprovals(part.parts);
    const pending =
      part.type === 'tool_call' &&
      part.status === 'awaiting_approval' &&
      !part.approvalAnswer;
    if (!pending && nested === part.parts) {
      return part;
    }
    return {
      ...part,
      ...(pending && {
        status: 'blocked' as const,
        approvalAnswer: 'expired' as const,
        resultPreview: part.resultPreview ?? EXPIRED_APPROVAL_PREVIEW,
      }),
      ...(nested && { parts: nested }),
    };
  });
  return settled.every((part, i) => part === parts[i]) ? parts : settled;
}

/**
 * A collaborator's turn (SS2) as the fork keeps it: the stub "continue
 * privately" writes. Their answer ran with their access, and the fork's own
 * runs see its whole history, so the copy must not carry what the owner's
 * runs in the original saw only as a stub.
 */
export function stubCollaboratorTurn(
  message: IMessage,
  forkerId: string,
): IMessage {
  // Persisted rows are read lean: a missing sender may be stored as null.
  const sender: unknown = message.senderId;
  if (sender === undefined || sender === null || idText(sender) === forkerId) {
    return message;
  }
  return {
    messageType: message.messageType,
    content: FOREIGN_TURN_STUB,
    contentFormat: 'MARKDOWN',
    citations: [],
    foreignTurn: true,
  } as unknown as IMessage;
}

/** Who sent a stored message (SS2): a collaborator's id, or null for the owner's own turn. */
/** A stored id as text: a string as is, an ObjectId as its hex. */
const idText = (id: { toString(): string }): string => id.toString();

const senderOf = (message: object): string | null => {
  // Persisted rows may store a missing sender as null.
  const sender: unknown = (message as { senderId?: unknown }).senderId;
  return sender === undefined || sender === null ? null : idText(sender);
};

function withExpiredApprovals(message: IMessage): IMessage {
  return Array.isArray(message.parts)
    ? { ...message, parts: expirePendingApprovals(message.parts) }
    : message;
}

export interface ForkSessionFields {
  sessionType: 'chat' | 'agent';
  agentKey?: string;
  /** Set only after the caller was confirmed to still have access to it. */
  projectId?: string;
}

export interface ForkChatSessionResult {
  conversation: IChatSessionDocument;
  messageCount: number;
}

/**
 * The source's project, or undefined when the caller can no longer see it: a
 * fork must not put a new chat into a project the caller lost access to.
 */
export async function resolveForkProjectId(
  source: { projectId?: { toString(): string } | null },
  orgId: string,
  userId: string,
): Promise<string | undefined> {
  if (!source.projectId) {
    return undefined;
  }
  // A stored chat holds a driver id, a repository row a string.
  const projectId = source.projectId.toString();
  try {
    await ProjectService.assertAccess(orgId, userId, projectId, 'viewer');
    return projectId;
  } catch (error) {
    if (error instanceof NotFoundError || error instanceof ForbiddenError) {
      return undefined;
    }
    throw error;
  }
}

/** The messages of a thread up to and including `lastSeq`, a batch at a time, oldest first. */
export async function* messagesUpTo(
  sessionId: string,
  orgId: string,
  lastSeq: number,
  tx?: Tx,
  repo: ConversationRepository = getConversationRepository(),
): AsyncGenerator<MessageRow[]> {
  for (let skip = 0; ; skip += FORK_COPY_BATCH_SIZE) {
    const rows = await repo.listMessages(
      sessionId,
      { orgId, skip, limit: FORK_COPY_BATCH_SIZE },
      tx,
    );
    const kept = rows.filter((r) => r.dto.seq <= lastSeq);
    if (kept.length > 0) {
      yield kept;
    }
    if (rows.length < FORK_COPY_BATCH_SIZE || kept.length < rows.length) {
      return;
    }
  }
}

/**
 * Gives the fork its own copy of every artifact the copied messages show.
 * An artifact that is gone or no longer readable is left as it is: its card
 * could not be opened from the original either. Any other failure aborts the
 * fork, because carrying on would leave the fork editing the original's
 * artifact.
 */
async function copyForkArtifacts(
  copyArtifacts: ArtifactCopier,
  sourceId: mongoose.Types.ObjectId,
  lastSeq: number,
  forkId: mongoose.Types.ObjectId,
  rowFilter: Record<string, unknown>,
): Promise<ArtifactCopyMap> {
  const withMarkers = await ChatSessionMessage.find(
    {
      sessionId: sourceId,
      seq: { $lte: lastSeq },
      ...NOT_REMOVED,
      ...rowFilter,
      content: { $regex: '::artifact\\[' },
    },
    { content: 1, senderId: 1 },
  ).lean();
  // Only the owner's own turns: a collaborator's artifacts are never copied (SS2).
  const refs = collectArtifactRefs(
    withMarkers
      .filter((row) => senderOf(row) === null)
      .map((row) => {
        const { content } = row as { content?: unknown };
        return typeof content === 'string' ? content : '';
      }),
  );
  if (refs.length === 0) {
    return new ArtifactCopyMap([]);
  }

  const outcome = await copyArtifacts(String(forkId), refs);
  const broken = outcome.failed.filter((f) => f.reason === 'error');
  if (broken.length > 0) {
    throw new InternalServerError(
      `Could not copy ${String(broken.length)} artifact(s) into the fork`,
    );
  }
  return new ArtifactCopyMap(outcome.copies);
}

/** A copied message's citation link as the id it names; persisted links are Mixed, so it may name none. */
function citationIdOf(link: unknown): string | null {
  if (link === null || typeof link !== 'object') return null;
  const id: unknown = (link as { citationId?: unknown }).citationId;
  return id === undefined || id === null ? null : idText(id);
}

/**
 * Each message's citations pointed at new citation documents of the fork's
 * own: erasing either conversation deletes the citation documents its
 * messages name, so a shared one would vanish from the other. A citation
 * whose document is already gone is dropped, as "continue privately" does.
 */
async function withCopiedCitations(
  messages: IMessage[],
  orgId: string,
  tx: Tx | undefined,
  repo: ConversationRepository,
): Promise<IMessage[]> {
  const ids = [
    ...new Set(
      messages.flatMap((message) =>
        (message.citations ?? []).flatMap((link) => {
          const id = citationIdOf(link);
          return id === null ? [] : [id];
        }),
      ),
    ),
  ];
  if (ids.length === 0) return messages;
  const stored = await repo.getCitations(ids, orgId);
  const created = await repo.createCitations(
    stored.map(({ content, chunkIndex, citationType, metadata }) => ({
      content,
      chunkIndex,
      citationType,
      metadata,
    })),
    tx,
  );
  // createCitations answers in the order it was given.
  const copyOf = new Map(
    stored.flatMap((source, i) => {
      const copy = created[i];
      return copy === undefined ? [] : [[source._id, copy._id] as const];
    }),
  );
  return messages.map((message) =>
    Array.isArray(message.citations) && message.citations.length > 0
      ? {
          ...message,
          citations: message.citations.flatMap((link) => {
            const id = citationIdOf(link);
            const copy = id === null ? undefined : copyOf.get(id);
            return copy === undefined
              ? []
              : [{ ...link, citationId: copy as never }];
          }),
        }
      : message,
  );
}

/** Artifacts copied for a fork before its chat is written. */
export interface ForkArtifacts {
  forkId: mongoose.Types.ObjectId;
  copies: ArtifactCopyMap;
}

/**
 * Copies the artifacts a fork will show, filed under `forkId`, before the
 * fork's chat is written. Call it outside any MongoDB transaction: every
 * version is uploaded again, so it can outlive MongoDB's 60-second transaction
 * limit, and `withTransaction` reruns its callback on a transient error, which
 * would copy everything a second time.
 */
export async function copyArtifactsForFork(
  source: IChatSessionDocument,
  messageId: string,
  forkId: mongoose.Types.ObjectId,
  copyArtifacts: ArtifactCopier,
  /** As `copySessionUpToMessage`'s: only the rows the fork will hold lend it artifacts. */
  rowFilter: Record<string, unknown> = {},
): Promise<ForkArtifacts> {
  const forkPoint = await ChatSessionMessage.findOne(
    { _id: messageId, sessionId: source._id, ...NOT_REMOVED, ...rowFilter },
    { seq: 1 },
  ).lean();
  if (!forkPoint) {
    throw new NotFoundError('Message not found');
  }
  const copies = await copyForkArtifacts(
    copyArtifacts,
    source._id,
    forkPoint.seq,
    forkId,
    rowFilter,
  );
  return { forkId, copies };
}

/**
 * Copy a chat session and every message up to `messageId` (inclusive) into a
 * new session owned by `userId`. The source is left unchanged. Share state is
 * not copied — the fork is private to the caller. The copy depends on nothing
 * of the original's: its messages name none of the original's runs
 * (`copied`), its citations are its own documents, a collaborator's turn is a
 * stub, and a durable original gives a durable fork with a clean workspace.
 * Messages are copied in batches so a long thread is never held in memory
 * whole. Without a
 * transaction, a failed copy removes what it already wrote.
 *
 * With `artifacts` (from `copyArtifactsForFork`), the fork is created under
 * the id they were filed under and its messages point at them, so editing in
 * one chat never changes the other.
 */
export async function copySessionUpToMessage(
  source: IChatSessionDocument,
  messageId: string,
  userId: string,
  orgId: string,
  fields: ForkSessionFields,
  mongoSession?: ClientSession | null,
  artifacts?: ForkArtifacts,
  /** Narrows the rows copied, for a source whose readers do not all see every row. */
  rowFilter: Record<string, unknown> = {},
): Promise<ForkChatSessionResult> {
  const sourceId = source._id;
  const sessionOption = mongoSession ? { session: mongoSession } : undefined;
  const forkPoint = await ChatSessionMessage.findOne(
    { _id: messageId, sessionId: sourceId, ...NOT_REMOVED, ...rowFilter },
    undefined,
    sessionOption,
  ).lean();

  if (!forkPoint) {
    throw new NotFoundError('Message not found');
  }

  // Counted before the session exists because its metadata is written once, at
  // creation, and the "forked from here" line is placed by this count.
  const copiedCount = await ChatSessionMessage.countDocuments({
    sessionId: sourceId,
    seq: { $lte: forkPoint.seq },
    ...NOT_REMOVED,
    ...rowFilter,
  }).session(mongoSession ?? null);

  const sessionFields: Partial<IChatSession> = {
    orgId: new mongoose.Types.ObjectId(orgId),
    userId: new mongoose.Types.ObjectId(userId),
    initiator: new mongoose.Types.ObjectId(userId),
    title: await forkTitleFor(
      {
        _id: String(sourceId),
        title: source.title,
        agentKey: source.agentKey,
      },
      userId,
      orgId,
    ),
    lastActivityAt: Date.now(),
    status: CONVERSATION_STATUS.COMPLETE,
    modelInfo: source.modelInfo,
    sessionType: fields.sessionType,
    isShared: false,
    isDeleted: false,
    isArchived: false,
    metadata: new Map<string, unknown>([
      ['forkedFromConversationId', String(sourceId)],
      ['forkedFromMessageId', messageId],
      ['forkedMessageCount', copiedCount],
    ]),
  };

  if (fields.sessionType === 'agent') {
    sessionFields.agentKey = fields.agentKey ?? source.agentKey;
    sessionFields.conversationSource = 'agent_chat';
  }

  if (fields.projectId) {
    sessionFields.projectId = new mongoose.Types.ObjectId(fields.projectId);
    sessionFields.projectVisibility = 'private';
  }

  const conversation = (await new ChatSession({
    ...sessionFields,
    ...(artifacts && { _id: artifacts.forkId }),
  }).save(sessionOption)) as IChatSessionDocument;
  const forkId = conversation._id;
  const unit = new MongoTx(mongoSession ?? undefined);
  // The fork keeps the original's executor; its first durable run imports the copied messages.
  await pinForkExecutor(
    String(forkId),
    orgId,
    await harnessVersionOf(String(sourceId), String(source.orgId), unit),
    unit,
  );
  const copies = artifacts?.copies ?? new ArtifactCopyMap([]);

  let messageCount = 0;
  try {
    let nextSeq = 0;
    for (;;) {
      const batch = await ChatSessionMessage.find(
        {
          sessionId: sourceId,
          seq: { $gte: nextSeq, $lte: forkPoint.seq },
          ...NOT_REMOVED,
          ...rowFilter,
        },
        undefined,
        sessionOption,
      )
        // Each copy keeps its answer's label; the append binds it to the copy's own version and text.
        .select('+provenance +labelHistory')
        .sort({ seq: 1 })
        .limit(FORK_COPY_BATCH_SIZE)
        .lean();
      if (batch.length === 0) {
        break;
      }
      const kept = await forkerMaySee(
        source,
        String(forkId),
        batch,
        userId,
        orgId,
      );
      if (kept.length > 0) {
        const copied = kept.map((row) =>
          pointMessageAtCopies(
            withExpiredApprovals(
              stubCollaboratorTurn(
                detachCopiedRuns(
                  stripMessageForCopy(
                    row as unknown as Record<string, unknown>,
                  ),
                ),
                userId,
              ),
            ),
            copies,
          ),
        );
        await appendMessages(
          forkId,
          conversation.orgId,
          await withCopiedCitations(
            copied,
            orgId,
            unit,
            getConversationRepository(),
          ),
          mongoSession,
        );
      }
      messageCount += kept.length;
      nextSeq = (batch[batch.length - 1] as { seq: number }).seq + 1;
    }
  } catch (error) {
    if (!mongoSession) {
      // row-writer-exempt: the fork never existed to any reader, so its rows go without a trace.
      await ChatSessionMessage.deleteMany({ sessionId: forkId });
      await ChatSession.deleteOne({ _id: forkId });
    }
    throw error;
  }

  return { conversation, messageCount };
}

export interface ForkOrigin {
  conversationId: string;
  messageId: string;
  /** The original chat's title; null when the viewer cannot open it (deleted, archived, not theirs). */
  title: string | null;
  /** This chat's copy of the fork point: the "forked from here" line goes after it. Absent on forks made before it was recorded. */
  atMessageId?: string;
}

const metaOf = (
  session: Pick<ChatSessionDto, 'metadata'>,
): Record<string, unknown> =>
  session.metadata instanceof Map
    ? Object.fromEntries(session.metadata)
    : (session.metadata ?? {});

/**
 * Where a fork was copied from, for the viewer of the fork. The original's
 * title is returned only when the viewer owns it, so a chat shared with
 * someone never names a chat that was not shared. Never throws: the origin is
 * decoration, and opening the chat must not depend on it.
 */
export async function resolveForkOrigin(
  session: Pick<ChatSessionDto, '_id' | 'orgId' | 'metadata' | 'agentKey'>,
  userId: string,
  repo: ConversationRepository = getConversationRepository(),
): Promise<ForkOrigin | undefined> {
  const meta = metaOf(session);
  const conversationId = meta.forkedFromConversationId;
  const messageId = meta.forkedFromMessageId;
  if (typeof conversationId !== 'string' || typeof messageId !== 'string') {
    return undefined;
  }
  try {
    const scope: SessionScope = session.agentKey
      ? { kind: 'agent', agentKey: session.agentKey }
      : { kind: 'chat' };
    const original = await repo.findOwned(scope, conversationId, {
      orgId: session.orgId,
      userId,
    });
    const copied = Number(meta.forkedMessageCount);
    const [atRow] =
      Number.isInteger(copied) && copied > 0
        ? await repo.listMessages(session._id, {
            orgId: session.orgId,
            skip: copied - 1,
            limit: 1,
          })
        : [];
    return {
      conversationId,
      messageId,
      title: original && !original.isArchived ? (original.title ?? '') : null,
      ...(atRow && { atMessageId: atRow.dto._id }),
    };
  } catch (error) {
    logger.warn('Could not resolve where a fork was copied from', {
      conversationId: session._id,
      error: error instanceof Error ? error.message : String(error),
    });
    return undefined;
  }
}

export interface ForkChild {
  conversationId: string;
  title: string;
  /** The message of this chat the fork was made at; the link goes after it. */
  fromMessageId: string;
}

/**
 * The viewer's own forks of a chat, so the original can link to them. Only the
 * viewer's own chats are listed: a fork someone else made of a shared chat is
 * theirs and is never named here. Never throws, like `resolveForkOrigin`.
 */
export async function resolveForks(
  session: Pick<ChatSessionDto, '_id' | 'orgId' | 'agentKey'>,
  userId: string,
  repo: ConversationRepository = getConversationRepository(),
): Promise<ForkChild[]> {
  try {
    const scope: SessionScope = session.agentKey
      ? { kind: 'agent', agentKey: session.agentKey }
      : { kind: 'chat' };
    const forks = await repo.listForksOf(scope, session._id, {
      orgId: session.orgId,
      userId,
    });
    return forks.flatMap((fork) => {
      const fromMessageId = metaOf(fork).forkedFromMessageId;
      return typeof fromMessageId === 'string'
        ? [{ conversationId: fork._id, title: fork.title ?? '', fromMessageId }]
        : [];
    });
  } catch (error) {
    logger.warn('Could not list the forks of a conversation', {
      conversationId: session._id,
      error: error instanceof Error ? error.message : String(error),
    });
    return [];
  }
}

/**
 * The rows of a batch the forker may take into a chat of their own: what they may see whole now. An
 * answer they see only because it was shared with them, or not at all, and a shared copy, stay behind: a share is for
 * the chat it was made in. So does an answer that reached them only through such a share (one built on a shared
 * answer's text): the rows are judged as the fork will read them, so the fork holds no answer that would only hide
 * there. So does another person's answer from before labels that they read through the view they had of the chat then:
 * that view is for the chat it was shared in. Another person's question keeps its words but not an
 * answer it quotes.
 */
/** Another person's rows a fork leaves behind when they come from before labels: an answer, an error and a card. */
const LEFT_BEHIND_BEFORE_LABELS = new Set([
  'bot_response',
  'error',
  'tool_call',
]);

async function forkerMaySee<R extends object>(
  source: IChatSessionDocument,
  forkId: string,
  batch: R[],
  userId: string,
  orgId: string,
): Promise<R[]> {
  const verdicts = await visibleRows(
    { userId, orgId, member: true },
    source,
    batch,
    { member: true, fresh: true, chatsRead: [forkId] },
  );
  const ownerId = idOfValue(source.initiator) ?? idOfValue(source.userId) ?? '';
  return batch.flatMap((row) => {
    const r = row as unknown as ReaderRow;
    const judged = verdicts.get(String(r._id));
    const mine = rowAuthorOf(r, ownerId) === idOfValue(userId);
    if (
      judged?.verdict !== 'visible' ||
      (!mine &&
        judged.basis === 'before_labels' &&
        LEFT_BEHIND_BEFORE_LABELS.has(String(r.messageType))) ||
      r.messageType === 'shared_answer'
    ) {
      return [];
    }
    return [mine ? row : withoutQuote(row)];
  });
}

const copyOf = (row: MessageRow): NewMessage =>
  withExpiredApprovals(
    stripMessageForCopy(row.dto as unknown as Record<string, unknown>),
  ) as unknown as NewMessage;

export interface CompareBranchResult {
  conversation: ChatSessionDto;
  messageCount: number;
}

/**
 * Compare models: a hidden copy of `source` holding only the messages before
 * the `user_query` `questionId`, owned by the caller and never shared. The
 * second model then answers that same question in it as an ordinary turn. The
 * question's seq is kept on the branch so a choice can cut the parent at the
 * same place even after a half-finished attempt. Artifacts are not copied: the
 * branch reads the parent's.
 */
export async function createCompareBranch(
  source: ChatSessionDto,
  question: { _id: string; seq: number },
  userId: string,
  orgId: string,
  tx?: Tx | null,
  repo: ConversationRepository = getConversationRepository(),
): Promise<CompareBranchResult> {
  const unit = tx ?? undefined;
  const conversation = await repo.createSession(
    {
      orgId,
      userId,
      initiator: userId,
      title: source.title,
      lastActivityAt: Date.now(),
      status: CONVERSATION_STATUS.COMPLETE,
      modelInfo: source.modelInfo,
      sessionType: 'chat',
      ...(source.projectId && {
        projectId: source.projectId,
        projectVisibility: 'private' as const,
      }),
      compareOf: source._id,
      compareFromMessageId: question._id,
      metadata: { compareFromSeq: question.seq },
    },
    unit,
  );
  let messageCount = 0;
  try {
    for await (const batch of messagesUpTo(
      source._id,
      orgId,
      question.seq - 1,
      unit,
      repo,
    )) {
      await repo.appendMessages(
        conversation._id,
        orgId,
        batch.map(copyOf),
        unit,
      );
      messageCount += batch.length;
    }
  } catch (error) {
    // Without a transaction nothing rolls back: remove the half-copied branch.
    if (!sessionOf(unit)) {
      await repo.discardSession(conversation._id, orgId);
    }
    throw error;
  }
  return { conversation, messageCount };
}

/**
 * Copies the artifacts the branch's own turn (its messages from `keep` on)
 * shows into `target`, so they outlive the branch. The thread before the
 * question is the branch's copy of the parent's, whose artifacts the branch
 * only read, so it is not copied.
 */
export async function copyBranchTurnArtifacts(
  branch: ChatSessionDto,
  keep: number,
  targetId: string,
  copyArtifacts: ArtifactCopier,
  repo: ConversationRepository = getConversationRepository(),
): Promise<{ copies: ArtifactCopyMap; failed: number }> {
  const contents: string[] = [];
  for (let skip = keep; ; skip += FORK_COPY_BATCH_SIZE) {
    const rows = await repo.listMessages(branch._id, {
      orgId: branch.orgId,
      skip,
      limit: FORK_COPY_BATCH_SIZE,
    });
    for (const r of rows) {
      if (typeof r.dto.content === 'string') contents.push(r.dto.content);
    }
    if (rows.length < FORK_COPY_BATCH_SIZE) break;
  }
  const refs = collectArtifactRefs(contents);
  if (refs.length === 0) return { copies: new ArtifactCopyMap([]), failed: 0 };
  const outcome = await copyArtifacts(targetId, refs);
  if (outcome.failed.length > 0) {
    logger.warn('Could not copy some artifacts of the chosen Compare answer', {
      failed: outcome.failed.length,
    });
  }
  return {
    copies: new ArtifactCopyMap(outcome.copies),
    failed: outcome.failed.length,
  };
}

/**
 * Compare models: makes `target` end with the turn the branch answered. The
 * target keeps its first `keep` messages (the thread before the question),
 * loses every later one, and gets the branch's messages after its own copy of
 * that thread. Truncating before appending makes a repeat after a failure
 * land in the same state.
 */
/** Whether a row is the chat owner's: its question's or note's author, or whoever asked for it; a row with neither is the owner's. */
const isOwnersRow = (row: MessageDto, ownerId: string): boolean => {
  const { authorUserId, requestedBy } = row as MessageDto & {
    authorUserId?: { toString(): string } | null;
    requestedBy?: { toString(): string } | null;
  };
  const by =
    row.messageType === 'user_query' || row.messageType === 'note'
      ? authorUserId
      : requestedBy;
  return (by?.toString() ?? ownerId) === ownerId;
};

const SOMEONE_ELSES_TURN =
  'Someone else has written in this chat since the compared question, so the other answer cannot replace it';

/** Whether any row of `target` from position `keep` on is someone other than its owner's. */
export async function othersWroteFrom(
  target: ChatSessionDto,
  keep: number,
  repo: ConversationRepository = getConversationRepository(),
): Promise<boolean> {
  for (let skip = keep; ; skip += FORK_COPY_BATCH_SIZE) {
    const rows = await repo.listMessages(target._id, {
      orgId: target.orgId,
      skip,
      limit: FORK_COPY_BATCH_SIZE,
    });
    if (rows.some((r) => !isOwnersRow(r.dto, target.userId))) {
      return true;
    }
    if (rows.length < FORK_COPY_BATCH_SIZE) return false;
  }
}

/** Refuses a choice that would replace someone else's rows: a Compare branch only ever replaces its owner's. */
export async function assertOnlyOwnersRowsFrom(
  target: ChatSessionDto,
  keep: number,
  repo: ConversationRepository = getConversationRepository(),
): Promise<void> {
  if (await othersWroteFrom(target, keep, repo)) {
    throw new ConflictError(SOMEONE_ELSES_TURN);
  }
}

export async function replaceTurnWithBranch(
  target: ChatSessionDto,
  branch: ChatSessionDto,
  keep: number,
  tx?: Tx | null,
  repo: ConversationRepository = getConversationRepository(),
  copies: ArtifactCopyMap = new ArtifactCopyMap([]),
): Promise<number> {
  const unit = tx ?? undefined;
  const orgId = target.orgId;
  const stale: string[] = [];
  for (let skip = keep; ; skip += FORK_COPY_BATCH_SIZE) {
    const rows = await repo.listMessages(
      target._id,
      { orgId, skip, limit: FORK_COPY_BATCH_SIZE },
      unit,
    );
    // Read again in the transaction: a row someone wrote since the caller's check is never deleted.
    if (rows.some((r) => !isOwnersRow(r.dto, target.userId))) {
      throw new ConflictError(SOMEONE_ELSES_TURN);
    }
    stale.push(...rows.map((r) => r.dto._id));
    if (rows.length < FORK_COPY_BATCH_SIZE) break;
  }
  await repo.deleteMessages(stale, orgId, unit, target._id);

  let moved = 0;
  for (let skip = keep; ; skip += FORK_COPY_BATCH_SIZE) {
    const rows = await repo.listMessages(
      branch._id,
      { orgId, skip, limit: FORK_COPY_BATCH_SIZE },
      unit,
    );
    if (rows.length > 0) {
      await repo.appendMessages(
        target._id,
        orgId,
        rows.map(
          (r) =>
            pointMessageAtCopies(
              copyOf(r) as unknown as IMessage,
              copies,
            ) as unknown as NewMessage,
        ),
        unit,
      );
      moved += rows.length;
    }
    if (rows.length < FORK_COPY_BATCH_SIZE) break;
  }
  return moved;
}
