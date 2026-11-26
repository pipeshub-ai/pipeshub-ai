import { NextFunction, Request, RequestHandler, Response } from 'express';
import { ChatTurnService } from '../application/chat-turns';
import { transportError } from './errors';
import { identityString, requestVia } from './request-identity';

export interface ChatCancelRouteOptions {
  kind: 'chat' | 'agent';
  /** False while the harness is off: every cancel goes to the legacy handler untouched. */
  enabled: () => boolean;
}

/**
 * The existing cancel routes (`POST …/cancel {runId}`) in front of today's
 * cancel (LAT2): a run id that names the conversation's running durable turn,
 * the one its client sent or the run's own, stops that run and answers as
 * today's cancel does; anything else is today's cancel. API and PAT clients
 * stop a durable turn the way they stopped a legacy one. Org and actor come
 * from the verified credential only.
 */
export function chatCancelMiddleware(
  service: ChatTurnService,
  options: ChatCancelRouteOptions,
): RequestHandler {
  const handle = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    if (!options.enabled()) {
      next();
      return;
    }
    const user = (req as Request & { user?: Record<string, unknown> }).user;
    const orgId = identityString(user?.['orgId']);
    const actorId = identityString(user?.['userId']);
    const sessionId = req.params['conversationId'];
    const runId = (req.body as { runId?: unknown } | undefined)?.runId;
    if (
      orgId === null ||
      actorId === null ||
      sessionId === undefined ||
      typeof runId !== 'string'
    ) {
      next();
      return;
    }
    let cancelled: boolean | null;
    try {
      cancelled = await service.cancel({
        orgId,
        actorId,
        sessionId,
        runId,
        ...(options.kind === 'agent'
          ? { agentKey: req.params['agentKey'] }
          : {}),
        via: requestVia(req),
        ...(req.context?.requestId !== undefined
          ? { requestId: req.context.requestId }
          : {}),
      });
    } catch (error) {
      next(transportError(error));
      return;
    }
    if (cancelled === null) {
      next();
      return;
    }
    res.status(200).json({ cancelled });
  };
  return (req, res, next) => {
    handle(req, res, next).catch(next);
  };
}
