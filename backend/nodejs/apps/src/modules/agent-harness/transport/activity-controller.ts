import { NextFunction, Response } from 'express';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { RunActivityService } from '../application/run-activity-service';
import { HarnessRun } from '../domain/execution';
import { transportError } from './errors';
import { responseSink, writeSseHead } from './sse';
import { viewerScope } from './stream-controller';

/**
 * `GET …/runs/:runId/activity`, `…/commands/:commandId/log` (UX5) and
 * `…/steps/:toolCallId` (ACTRESULTS): the activity panel's stream, a
 * command's full log and an opened step's result, for the run's owner only. Closing the stream never touches the run.
 */
export class RunActivityController {
  constructor(
    private readonly activity: RunActivityService,
    private readonly kind: 'agent' | 'chat',
  ) {}

  stream = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    let run: HarnessRun;
    try {
      run = await this.activity.authorize(
        viewerScope(req, this.kind),
        req.params['runId'] as string,
      );
    } catch (error) {
      next(transportError(error));
      return;
    }
    writeSseHead(res);
    const handle = this.activity.stream(
      run,
      responseSink(res),
      req.context?.requestId,
    );
    res.on('close', () => {
      handle.close('client_closed');
    });
    await handle.done;
  };

  step = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const step = await this.activity.step(
        viewerScope(req, this.kind),
        req.params['runId'] as string,
        req.params['toolCallId'] as string,
      );
      // The owner's own result: never kept by a shared cache.
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'private, no-store')
        .json({ step });
    } catch (error) {
      next(transportError(error));
    }
  };

  log = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const log = await this.activity.log(
        viewerScope(req, this.kind),
        req.params['runId'] as string,
        req.params['commandId'] as string,
      );
      res
        .status(HTTP_STATUS.OK)
        .type('text/plain; charset=utf-8')
        .setHeader('X-Content-Type-Options', 'nosniff')
        .send(log);
    } catch (error) {
      next(transportError(error));
    }
  };
}
