import { NextFunction, Response } from 'express';
import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { SessionForkService } from '../../agent-harness/application/session-fork-service';
import { WorkspaceError } from '../../agent-harness/domain/workspace';
import { transportError } from '../../agent-harness/transport/errors';
import { workspaceRefusal } from '../../agent-harness/transport/workspace-controller';
import { getCurrentOrgId } from '../../../libs/context/orgContext';

/** The fork service where the harness is on for the request's org; undefined otherwise. */
export type SessionForkSource = () =>
  | SessionForkService<StoreSession>
  | undefined
  | Promise<SessionForkService<StoreSession> | undefined>;

/**
 * POST /conversations/:conversationId/fork and
 * /agents/:agentKey/conversations/:conversationId/fork (SS3): the caller
 * continues the conversation privately. Who forks and in which org come from
 * the verified credential; the body names only the request key. 201 with the
 * new conversation, 200 when the same request was answered before.
 */
export const forkConversation =
  (forks: SessionForkSource) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const service = await forks();
      if (service === undefined) {
        throw new ServiceUnavailableError(
          'Continuing a conversation privately is not available',
        );
      }
      const orgId: unknown = getCurrentOrgId();
      const actorId: unknown = req.user?.userId;
      if (
        typeof orgId !== 'string' ||
        typeof actorId !== 'string' ||
        orgId === '' ||
        actorId === ''
      )
        throw new UnauthorizedError('User not authenticated');
      const { conversationId, agentKey } = req.params as {
        conversationId: string;
        agentKey?: string;
      };
      const { idempotencyKey } = req.body as { idempotencyKey: string };
      const outcome = await service.fork({
        orgId,
        actorId,
        sessionId: conversationId,
        ...(agentKey !== undefined ? { agentKey } : {}),
        requestKey: idempotencyKey,
        authorization: req.headers.authorization,
        ...(req.context?.requestId !== undefined
          ? { requestId: req.context.requestId }
          : {}),
      });
      res.status(outcome.replayed ? HTTP_STATUS.OK : HTTP_STATUS.CREATED).json({
        conversationId: outcome.sessionId,
        replayed: outcome.replayed,
      });
    } catch (error) {
      next(
        error instanceof WorkspaceError
          ? workspaceRefusal(error)
          : transportError(error),
      );
    }
  };
