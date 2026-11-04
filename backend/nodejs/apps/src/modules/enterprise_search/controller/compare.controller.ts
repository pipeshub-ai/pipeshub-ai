import { NextFunction, Response } from 'express';
import { createUnitOfWork } from '../../../libs/repositories/doc-store';
import { refuseIfDraining } from '../../../libs/repositories/drain';
import { isValidId } from '../../../libs/repositories/ids';
import {
  getConversationRepository,
  type ChatSessionDto,
  type ConversationRepository,
  type RunReservation,
  type SessionActor,
} from '../../../libs/repositories/conversation';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  ConflictError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { CONVERSATION_STATUS } from '../constants/constants';
import { Logger } from '../../../libs/services/logger.service';
import {
  collectArtifactRefs,
  type ArtifactCopier,
} from '../utils/fork-artifacts';
import { MAX_COMPARE_MODELS } from '../utils/chat-compare-feature';
import {
  copyBranchTurnArtifacts,
  createCompareBranch,
  messagesUpTo,
  replaceTurnWithBranch,
} from '../utils/fork-conversation';

const repo = (): ConversationRepository => getConversationRepository();
const unitOfWork = createUnitOfWork(
  process.env.REPLICA_SET_AVAILABLE === 'true',
);
const CHAT_SCOPE = { kind: 'chat' } as const;

/** Branches one question may have next to the conversation itself. */
export const MAX_COMPARE_BRANCHES = MAX_COMPARE_MODELS - 1;

const logger = Logger.getInstance({ service: 'CompareController' });

/** Branches nobody chose (tab closed) are swept when their owner next opens one. */
const STALE_BRANCH_MS = 24 * 60 * 60 * 1000;

/** The artifact side of a branch's life; absent where there is no connectors service (tests). */
export interface CompareArtifacts {
  copier: (req: AuthenticatedUserRequest) => ArtifactCopier;
  /** Deletes what the caller owns under a conversation id. */
  remove: (
    conversationId: string,
    req: AuthenticatedUserRequest,
  ) => Promise<void>;
  /** Deletes one artifact the caller owns. */
  removeOne: (
    artifactId: string,
    req: AuthenticatedUserRequest,
  ) => Promise<void>;
}

/** Same window reserveRun uses to tell a live run from a dead one. */
const ACTIVE_RUN_WINDOW_MS = 10 * 60 * 1000;
const isLive = (b: ChatSessionDto): boolean =>
  b.status === CONVERSATION_STATUS.INPROGRESS &&
  (b.lastActivityAt ?? 0) > Date.now() - ACTIVE_RUN_WINDOW_MS;

/** Every message body from `skip` on, up to `end` (exclusive) when given. */
const contentsOf = async (
  c: ChatSessionDto,
  skip: number,
  end = Infinity,
): Promise<string[]> => {
  const out: string[] = [];
  for (;;) {
    const limit = Math.min(200, end - skip);
    if (limit <= 0) break;
    const rows = await repo().listMessages(c._id, {
      orgId: c.orgId,
      skip,
      limit,
    });
    for (const r of rows) {
      if (typeof r.dto.content === 'string') out.push(r.dto.content);
    }
    if (rows.length < limit) break;
    skip += limit;
  }
  return out;
};

/** Best effort: a branch artifact left behind is invisible, never worth failing the request. */
const removeArtifacts = async (
  artifacts: CompareArtifacts | undefined,
  ids: string[],
  req: AuthenticatedUserRequest,
): Promise<void> => {
  if (!artifacts) return;
  for (const id of ids) {
    await artifacts.remove(id, req).catch((error: unknown) => {
      logger.warn('Could not delete the artifacts of a Compare branch', {
        branchId: id,
        error: error instanceof Error ? error.message : String(error),
      });
    });
  }
};

/** Removes the parent's branches and their artifacts. */
const discardWithArtifacts = async (
  parent: ChatSessionDto,
  actor: SessionActor,
  req: AuthenticatedUserRequest,
  artifacts: CompareArtifacts | undefined,
  keepLive = false,
): Promise<void> => {
  // A new question from another tab must not kill a branch that tab is still answering.
  const ids = (
    await repo().listBranches(parent.orgId, parent._id, actor.userId)
  )
    .filter((b) => !(keepLive && isLive(b)))
    .map((b) => b._id);
  if (ids.length === 0) return;
  await repo().discardBranches(parent.orgId, parent._id, ids);
  await removeArtifacts(artifacts, ids, req);
};

/** Route guard for archiving or deleting a chat: its branches' artifacts go with it. */
export const purgeBranchArtifacts =
  (artifacts: CompareArtifacts) =>
  async (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const actor = actorOf(req);
      const id = req.params.conversationId as string;
      if (isValidId(id)) {
        const branches = await repo().listBranches(
          actor.orgId,
          id,
          actor.userId,
        );
        await removeArtifacts(
          artifacts,
          branches.map((b) => b._id),
          req,
        );
      }
      next();
    } catch (error) {
      next(error);
    }
  };

/** Sweeps the caller's branches older than a day, across all their chats. */
const sweepStaleBranches = async (
  actor: SessionActor,
  req: AuthenticatedUserRequest,
  artifacts: CompareArtifacts | undefined,
): Promise<void> => {
  try {
    const stale = await repo().listStaleBranches(
      actor.orgId,
      actor.userId,
      Date.now() - STALE_BRANCH_MS,
    );
    for (const b of stale) {
      if (!b.compareOf) continue;
      await repo().discardBranches(b.orgId, b.compareOf, [b._id]);
      await removeArtifacts(artifacts, [b._id], req);
    }
  } catch (error) {
    logger.warn('Could not sweep stale Compare branches', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

const actorOf = (req: AuthenticatedUserRequest): SessionActor => ({
  orgId: String(req.user?.orgId),
  userId: String(req.user?.userId),
});

/** The caller's own plain chat. 404 when it is not theirs; 400 when it is an agent chat. */
const loadParent = async (
  req: AuthenticatedUserRequest,
): Promise<ChatSessionDto> => {
  const actor = actorOf(req);
  const id = req.params.conversationId as string;
  const found = isValidId(id)
    ? await repo().findOwned(CHAT_SCOPE, id, actor)
    : null;
  if (!found || found.isArchived || found.compareOf) {
    const agentChat = isValidId(id)
      ? await repo().findOwned({ kind: 'agent' }, id, actor)
      : null;
    if (agentChat) {
      throw new BadRequestError(
        'Compare models is not available in agent chats',
      );
    }
    throw new NotFoundError('Conversation not found');
  }
  return found;
};

const reserve = async (
  id: string,
  actor: SessionActor,
  what: string,
): Promise<RunReservation> => {
  try {
    // No settle wait: a running answer is an immediate 409, not a 15 second poll.
    const reservation = await repo().reserveRun(CHAT_SCOPE, id, actor, 0);
    if (!reservation) throw new NotFoundError('Conversation not found');
    return reservation;
  } catch (error) {
    if (error instanceof ConflictError) {
      throw new ConflictError(
        `${what} is still being answered. Wait for it to finish, or stop it, then choose.`,
      );
    }
    throw error;
  }
};

/** POST /:conversationId/compare/branch */
export const createCompareBranchHandler =
  (artifacts?: CompareArtifacts) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (await refuseIfDraining(res)) return;
      const actor = actorOf(req);
      const { userMessageId } = req.body as { userMessageId: string };
      const parent = await loadParent(req);
      const question = await repo().findMessage(
        parent._id,
        parent.orgId,
        userMessageId,
      );
      if (!question || question.messageType !== 'user_query') {
        throw new NotFoundError('Message not found');
      }

      // A branch of an earlier try is stale the moment the question is asked again.
      await discardWithArtifacts(parent, actor, req, artifacts, true);
      await sweepStaleBranches(actor, req, artifacts);
      const { conversation } = await unitOfWork.run((tx) =>
        createCompareBranch(parent, question, actor.userId, actor.orgId, tx),
      );

      // Two requests racing past the discard: the older branch stays, the newer is dropped.
      const mine = (
        await repo().listBranches(parent.orgId, parent._id, actor.userId)
      ).filter((b) => b.compareFromMessageId === question._id);
      if (
        !mine
          .slice(0, MAX_COMPARE_BRANCHES)
          .some((b) => b._id === conversation._id)
      ) {
        await repo().discardBranches(parent.orgId, parent._id, [
          conversation._id,
        ]);
        throw new ConflictError('This question already has a Compare branch');
      }
      res.status(200).json({ branchConversationId: conversation._id });
    } catch (error) {
      next(error);
    }
  };

/** GET /:conversationId/compare */
export const listCompareBranches =
  () =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const parent = await loadParent(req);
      const branches = await repo().listBranches(
        parent.orgId,
        parent._id,
        actorOf(req).userId,
      );
      res.status(200).json({
        branches: branches.map((b) => ({
          branchConversationId: b._id,
          userMessageId: b.compareFromMessageId,
        })),
      });
    } catch (error) {
      next(error);
    }
  };

const fromSeqOf = (branch: ChatSessionDto): number => {
  const meta = branch.metadata;
  const value =
    meta instanceof Map ? meta.get('compareFromSeq') : meta?.compareFromSeq;
  if (typeof value !== 'number') {
    throw new InternalServerError('Compare branch is missing its start point');
  }
  return value;
};

/** POST /:conversationId/compare/choose */
export const chooseCompare =
  (artifacts?: CompareArtifacts) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const reservations: RunReservation[] = [];
    try {
      if (await refuseIfDraining(res)) return;
      const actor = actorOf(req);
      const { choice } = req.body as { choice: string };
      const parent = await loadParent(req);

      if (choice === 'primary') {
        await discardWithArtifacts(parent, actor, req, artifacts);
        res.status(200).json({ ok: true });
        return;
      }

      const branches = await repo().listBranches(
        parent.orgId,
        parent._id,
        actor.userId,
      );
      const branch = branches.find((b) => b._id === choice.toLowerCase());
      if (!branch) {
        // A retry after the choice went through: nothing is left to choose from.
        if (branches.length === 0) {
          res.status(200).json({ ok: true, already: true });
          return;
        }
        throw new NotFoundError('Compare branch not found');
      }

      // The reservations also keep a second choose, or a new turn, out while this one writes.
      reservations.push(await reserve(branch._id, actor, 'The other answer'));
      reservations.push(await reserve(parent._id, actor, 'This conversation'));

      const fromSeq = fromSeqOf(branch);
      let keep = 0;
      for await (const batch of messagesUpTo(
        parent._id,
        parent.orgId,
        fromSeq - 1,
      )) {
        keep += batch.length;
      }
      const total = await repo().countMessages(branch._id, branch.orgId);
      if (total - keep < 2) {
        throw new ConflictError('The other answer has no response to keep yet');
      }

      // Filed under the branch, which is deleted below: copy them under the parent first.
      const moved = artifacts
        ? await copyBranchTurnArtifacts(
            branch,
            keep,
            parent._id,
            artifacts.copier(req),
          )
        : undefined;
      // What the parent's discarded turn made, except what the kept thread also shows (an edited artifact).
      const kept = new Set(
        collectArtifactRefs(await contentsOf(parent, 0, keep)).map(
          (r) => r.artifactId,
        ),
      );
      const dropped = artifacts
        ? collectArtifactRefs(await contentsOf(parent, keep))
            .map((r) => r.artifactId)
            .filter((id) => !kept.has(id))
        : [];
      // The originals of an artifact that could not be copied stay: the moved card still points at them.
      const branchIds = (
        await repo().listBranches(parent.orgId, parent._id, actor.userId)
      )
        .map((b) => b._id)
        .filter((id) => id !== branch._id || !moved?.failed);

      await unitOfWork.run(async (tx) => {
        await replaceTurnWithBranch(
          parent,
          branch,
          keep,
          tx,
          undefined,
          moved?.copies,
        );
        await repo().recordRunState(
          parent._id,
          {
            // A branch whose run died mid-way still says Inprogress; the parent must not inherit that.
            status:
              !branch.status || branch.status === CONVERSATION_STATUS.INPROGRESS
                ? CONVERSATION_STATUS.FAILED
                : branch.status,
            failReason: branch.failReason ?? null,
            modelInfo: branch.modelInfo,
            lastActivityAt: Date.now(),
          },
          tx,
        );
        await repo().discardBranches(parent.orgId, parent._id, undefined, tx);
      });
      await removeArtifacts(artifacts, branchIds, req);
      for (const id of dropped) {
        await artifacts?.removeOne(id, req).catch((error: unknown) => {
          logger.warn('Could not delete an artifact of the replaced answer', {
            artifactId: id,
            error: error instanceof Error ? error.message : String(error),
          });
        });
      }
      res.status(200).json({ ok: true });
    } catch (error) {
      next(error);
    } finally {
      // A no-op once the run state was rewritten or the branch deleted.
      await Promise.all(
        reservations.map((r) =>
          repo()
            .releaseRun(r)
            .catch(() => undefined),
        ),
      );
    }
  };
