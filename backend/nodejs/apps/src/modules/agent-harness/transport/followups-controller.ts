import { NextFunction, Response } from 'express';
import { z, ZodError } from 'zod';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ExecutionService } from '../application/execution-service';
import { HarnessAuthority } from '../application/harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';
import { RunFollowupsService } from '../application/run-followups-service';
import {
  HarnessUnauthorizedError,
  RunNotCompletedError,
} from '../domain/errors';
import { transportError as internalTransportError } from './internal-controller';
import { followupItemsSchema } from '../domain/followups';
import { followupsWaitQuery } from './schemas';
import { transportError as publicError } from './errors';
import { HarnessAttemptRequest, logAuthorityRefusal } from './internal-auth';
import { attemptClaims, transportError } from './internal-controller';
import { viewerScope } from './stream-controller';
import { contract } from '../contracts/zod-contract';

const boundedId = z.string().min(1).max(256);

export const offerFollowupsSchema = z.object({
  params: z.object({ runId: boundedId }),
  body: z
    .object({
      attemptId: boundedId,
      fence: z.number().int().nonnegative(),
      items: contract(followupItemsSchema),
    })
    .strict(),
});

/**
 * `POST …/internal/runs/:runId/followups` (UX5): the worker stores a
 * completed run's suggested next questions after its terminal commit, with
 * the attempt token that ran it. The first write wins; a retry answers what
 * it stored.
 */
export class AgentHarnessFollowupsController {
  constructor(
    private readonly executions: ExecutionService,
    private readonly authority: HarnessAuthority,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  offer = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const { body } = offerFollowupsSchema.parse({
        params: req.params,
        body: req.body as unknown,
      });
      await this.authority.authorizeAttempt(claims, body);
      const followups = await this.executions.storeFollowups(
        claims.orgId,
        claims.runId,
        body.items,
      );
      res.status(HTTP_STATUS.OK).json({ followups });
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  private refused(req: HarnessAttemptRequest, error: unknown): unknown {
    if (error instanceof HarnessUnauthorizedError) {
      logAuthorityRefusal(this.logger, req, error);
    }
    if (error instanceof ZodError) {
      this.logger.warn(
        'followups.refused',
        harnessLogMeta('followups.refused', {
          orgId: req.harnessAttempt?.orgId,
          runId: req.harnessAttempt?.runId,
          reason: 'invalid_request',
        }),
      );
      return new BadRequestError('Invalid follow-ups');
    }
    if (error instanceof RunNotCompletedError)
      return internalTransportError(error);
    return transportError(error);
  }
}

/** `GET …/runs/:runId/followups?waitMs=` (UX5): the run's owner reads them, waiting up to `waitMs`. */
export class RunFollowupsController {
  constructor(
    private readonly followups: RunFollowupsService,
    private readonly kind: 'agent' | 'chat',
  ) {}

  read = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const left = new AbortController();
    const onClose = (): void => {
      left.abort();
    };
    res.on('close', onClose);
    try {
      const { waitMs } = followupsWaitQuery.parse(req.query);
      const followups = await this.followups.read(
        viewerScope(req, this.kind),
        req.params['runId'] as string,
        waitMs,
        left.signal,
      );
      if (left.signal.aborted) return;
      res.setHeader('Cache-Control', 'no-store');
      res.status(HTTP_STATUS.OK).json({ followups });
    } catch (error) {
      next(publicError(error));
    } finally {
      res.off('close', onClose);
    }
  };
}
