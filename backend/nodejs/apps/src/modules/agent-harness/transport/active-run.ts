import { NextFunction, Request, RequestHandler, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { SessionRunService } from '../application/session-run-service';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';
import { identityString } from './request-identity';

/**
 * Adds `conversation.activeRunId` to the conversation GET of a durable
 * session (REPORT 8; the P0.13 follow-up): the run to reattach to, or
 * `null`, saving the client a discovery request per conversation load. A
 * legacy session's response, and every response while the harness is off,
 * is left exactly as it was (L-21).
 */
export function activeRunIdMiddleware(
  runs: SessionRunService,
  kind: 'agent' | 'chat',
  enabled: () => boolean,
  logger: HarnessLogger = silentHarnessLogger,
): RequestHandler {
  return (req: Request, res: Response, next: NextFunction): void => {
    if (!enabled()) {
      next();
      return;
    }
    const user = (req as AuthenticatedUserRequest).user as
      Record<string, unknown> | undefined;
    const orgId = identityString(user?.['orgId']);
    const actorId = identityString(user?.['userId']);
    const sessionId = req.params['conversationId'];
    if (orgId === null || actorId === null || !sessionId) {
      next();
      return;
    }
    runs
      .activeRunId({
        orgId,
        actorId,
        sessionId,
        ...(kind === 'agent' ? { agentKey: req.params['agentKey'] } : {}),
      })
      .then((activeRunId) => {
        if (activeRunId !== undefined) {
          const json = res.json.bind(res);
          res.json = (body: unknown) => json(withActiveRun(body, activeRunId));
        }
        next();
      })
      .catch((error: unknown) => {
        logger.warn(
          'run.active_lookup_failed',
          harnessLogMeta('run.active_lookup_failed', {
            orgId,
            sessionId,
            errorType: error instanceof Error ? error.name : typeof error,
            choice: 'response_without_active_run',
          }),
        );
        next();
      });
  };
}

function withActiveRun(body: unknown, activeRunId: string | null): unknown {
  if (body === null || typeof body !== 'object') return body;
  const conversation = (body as { conversation?: unknown }).conversation;
  if (conversation === null || typeof conversation !== 'object') return body;
  return { ...body, conversation: { ...conversation, activeRunId } };
}
