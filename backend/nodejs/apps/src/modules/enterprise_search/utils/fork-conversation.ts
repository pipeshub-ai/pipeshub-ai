import {
  ForbiddenError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { ProjectService } from '../../projects/services/project.service';
import { CONVERSATION_STATUS } from '../constants/constants';
import { getConversationRepository } from '../../../libs/repositories/conversation';
import type {
  ChatSessionDto,
  MessageRow,
  NewMessage,
} from '../../../libs/repositories/conversation/types';
import type { ConversationRepository } from '../../../libs/repositories/conversation';
import { sessionOf } from '../../../libs/repositories/mongo';
import type { Tx } from '../../../libs/repositories/uow';
import type { IMessage, IMessagePart } from '../types/conversation.interfaces';
import {
  ArtifactCopier,
  ArtifactCopyMap,
  collectArtifactRefs,
  pointMessageAtCopies,
} from './fork-artifacts';

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
]);

export function buildForkTitle(originalTitle?: string | null): string {
  const base = (originalTitle ?? '').trim() || 'Untitled';
  const titled = `${FORK_TITLE_PREFIX}${base}`;
  return titled.length <= FORK_TITLE_MAX_LENGTH
    ? titled
    : titled.slice(0, FORK_TITLE_MAX_LENGTH);
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
  conversation: ChatSessionDto;
  messageCount: number;
}

/**
 * The source's project, or undefined when the caller can no longer see it: a
 * fork must not put a new chat into a project the caller lost access to.
 */
export async function resolveForkProjectId(
  source: ChatSessionDto,
  orgId: string,
  userId: string,
): Promise<string | undefined> {
  if (!source.projectId) {
    return undefined;
  }
  try {
    await ProjectService.assertAccess(
      orgId,
      userId,
      source.projectId,
      'viewer',
    );
    return source.projectId;
  } catch (error) {
    if (error instanceof NotFoundError || error instanceof ForbiddenError) {
      return undefined;
    }
    throw error;
  }
}

/** The messages of a thread up to and including `lastSeq`, a batch at a time, oldest first. */
async function* messagesUpTo(
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
  source: ChatSessionDto,
  lastSeq: number,
  forkId: string,
): Promise<ArtifactCopyMap> {
  const contents = await getConversationRepository().listArtifactMarkerContents(
    source._id,
    source.orgId,
    lastSeq,
  );
  const refs = collectArtifactRefs(contents);
  if (refs.length === 0) {
    return new ArtifactCopyMap([]);
  }

  const outcome = await copyArtifacts(forkId, refs);
  const broken = outcome.failed.filter((f) => f.reason === 'error');
  if (broken.length > 0) {
    throw new InternalServerError(
      `Could not copy ${String(broken.length)} artifact(s) into the fork`,
    );
  }
  return new ArtifactCopyMap(outcome.copies);
}

/** Artifacts copied for a fork before its chat is written. */
export interface ForkArtifacts {
  forkId: string;
  copies: ArtifactCopyMap;
}

/**
 * Copies the artifacts a fork will show, filed under `forkId`, before the
 * fork's chat is written. Call it outside the unit of work: every version is
 * uploaded again, so it can outlive a database transaction, and a retried
 * unit would copy everything a second time.
 */
export async function copyArtifactsForFork(
  source: ChatSessionDto,
  messageId: string,
  forkId: string,
  copyArtifacts: ArtifactCopier,
): Promise<ForkArtifacts> {
  const forkPoint = await getConversationRepository().findMessage(
    source._id,
    source.orgId,
    messageId,
  );
  if (!forkPoint) {
    throw new NotFoundError('Message not found');
  }
  const copies = await copyForkArtifacts(
    copyArtifacts,
    source,
    forkPoint.seq,
    forkId,
  );
  return { forkId, copies };
}

/**
 * Copy a chat session and every message up to `messageId` (inclusive) into a
 * new session owned by `userId`. The source is left unchanged. Share state is
 * not copied: the fork is private to the caller. Messages are copied in
 * batches so a long thread is never held in memory whole. Without a
 * transaction (a store that cannot give one), a failed copy hides what it
 * already wrote.
 *
 * With `artifacts` (from `copyArtifactsForFork`), the fork is created under
 * the id they were filed under and its messages point at them, so editing in
 * one chat never changes the other.
 */
export async function copySessionUpToMessage(
  source: ChatSessionDto,
  messageId: string,
  userId: string,
  orgId: string,
  fields: ForkSessionFields,
  tx?: Tx | null,
  artifacts?: ForkArtifacts,
  repo: ConversationRepository = getConversationRepository(),
): Promise<ForkChatSessionResult> {
  const unit = tx ?? undefined;
  const forkPoint = await repo.findMessage(source._id, orgId, messageId, unit);
  if (!forkPoint) {
    throw new NotFoundError('Message not found');
  }

  const conversation = await repo.createSession(
    {
      ...(artifacts && { id: artifacts.forkId }),
      orgId,
      userId,
      initiator: userId,
      title: buildForkTitle(source.title),
      lastActivityAt: Date.now(),
      status: CONVERSATION_STATUS.COMPLETE,
      modelInfo: source.modelInfo,
      sessionType: fields.sessionType,
      ...(fields.sessionType === 'agent' && {
        agentKey: fields.agentKey ?? source.agentKey,
        conversationSource: 'agent_chat' as const,
      }),
      ...(fields.projectId && {
        projectId: fields.projectId,
        projectVisibility: 'private' as const,
      }),
      metadata: {
        forkedFromConversationId: source._id,
        forkedFromMessageId: messageId,
      },
    },
    unit,
  );
  const copies = artifacts?.copies ?? new ArtifactCopyMap([]);

  let messageCount = 0;
  try {
    for await (const batch of messagesUpTo(
      source._id,
      orgId,
      forkPoint.seq,
      unit,
      repo,
    )) {
      await repo.appendMessages(
        conversation._id,
        orgId,
        batch.map(
          (row) =>
            pointMessageAtCopies(
              withExpiredApprovals(
                stripMessageForCopy(
                  row.dto as unknown as Record<string, unknown>,
                ),
              ),
              copies,
            ) as unknown as NewMessage,
        ),
        unit,
      );
      messageCount += batch.length;
    }
  } catch (error) {
    // Without a transaction (no replica set) nothing rolls back: remove the half-copied fork.
    if (!sessionOf(unit)) {
      await repo.discardSession(conversation._id, orgId);
    }
    throw error;
  }

  return { conversation, messageCount };
}
