import { NextFunction, Request, Response } from 'express';
import {
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { SessionRunService } from '../application/session-run-service';
import { HarnessNotFoundError } from '../domain/errors';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import { framesOf } from '../projection/frame-version';
import { RunStreamService } from '../projection/stream-service';
import type { RunWatchRegistry } from '../application/run-end-notices';
import { identityString } from './request-identity';
import { responseSink, writeSseHead } from './sse';

export function viewerScope(
  req: AuthenticatedUserRequest,
  kind: 'agent' | 'chat',
): SessionExecutionScope {
  const user = req.user as Record<string, unknown> | undefined;
  const orgId = identityString(user?.['orgId']);
  const actorId = identityString(user?.['userId']);
  if (orgId === null || actorId === null) {
    throw new UnauthorizedError('Authenticated user identity is incomplete');
  }
  return {
    orgId,
    actorId,
    sessionId: req.params['sessionId'] as string,
    ...(kind === 'agent' ? { agentKey: req.params['agentKey'] } : {}),
  };
}

/**
 * `GET …/runs/:runId/stream` (REPORT 6.2.4, 8): the run's AG-UI frames over
 * SSE with an `id:` on each, resumable with `Last-Event-ID`. The viewer's
 * audience comes from the session (owner or shared), never from the request.
 * Closing the stream never stops the run.
 */
export class RunStreamController {
  constructor(
    private readonly runs: SessionRunService,
    private readonly streams: RunStreamService,
    private readonly kind: 'agent' | 'chat',
    /** G2 (ADR-INBOX-01): while the stream is open its viewer watches the conversation; absent, nobody is counted. */
    private readonly watchers?: Pick<RunWatchRegistry, 'watch'>,
  ) {}

  stream = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    let access;
    try {
      access = await this.runs.streamAccess(
        viewerScope(req, this.kind),
        req.params['runId'] as string,
      );
    } catch (error) {
      next(
        error instanceof HarnessNotFoundError
          ? new NotFoundError('Agent run not found')
          : error,
      );
      return;
    }
    const lastEventId =
      (req as Request).header('last-event-id') ??
      (typeof req.query['lastEventId'] === 'string'
        ? req.query['lastEventId']
        : undefined);
    writeSseHead(res);
    const scope = viewerScope(req, this.kind);
    const unwatch = this.watchers?.watch({
      orgId: scope.orgId,
      userId: scope.actorId,
      sessionId: access.run.sessionId,
    });
    const handle = this.streams.open(
      {
        run: access.run,
        audience: access.access.audience,
        ...(access.access.title !== undefined
          ? { title: access.access.title }
          : {}),
        ...(lastEventId !== undefined ? { lastEventId } : {}),
        ...(req.context?.requestId !== undefined
          ? { requestId: req.context.requestId }
          : {}),
        ...(req.headers.authorization !== undefined
          ? { authorization: req.headers.authorization }
          : {}),
        ...framesOf(req),
      },
      responseSink(res),
    );
    res.on('close', () => {
      unwatch?.();
      handle.close('client_closed');
    });
    try {
      await handle.done;
    } finally {
      unwatch?.();
    }
  };
}
