import { NextFunction, Response } from 'express';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { ChildRunService } from '../application/child-run-service';
import { HarnessAuthority } from '../application/harness-authority';
import { HarnessLogger, silentHarnessLogger } from '../application/harness-log';
import { HarnessUnauthorizedError } from '../domain/errors';
import { HarnessAttemptRequest, logAuthorityRefusal } from './internal-auth';
import { attemptClaims, transportError } from './internal-controller';
import { spawnChildRunSchema } from './schemas';

/** `POST /runs/:runId/children`: the parent's attempt token names the parent, org and worker. */
export class AgentHarnessChildController {
  constructor(
    private readonly children: ChildRunService,
    private readonly authority: HarnessAuthority,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  spawn = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const body = spawnChildRunSchema.shape.body.parse(req.body);
      await this.authority.authorizeAttempt(claims, body);
      const spawned = await this.children.spawn(
        {
          orgId: claims.orgId,
          runId: claims.runId,
          workerId: claims.workerId,
          attemptId: body.attemptId,
          fence: body.fence,
        },
        {
          invocationId: body.invocationId,
          ...(body.taskId !== undefined ? { taskId: body.taskId } : {}),
          goal: body.goal,
          grants: body.grants,
          ...(body.subAgent !== undefined ? { subAgent: body.subAgent } : {}),
          ...(body.executor !== undefined ? { executor: body.executor } : {}),
          ...(body.inheritedLabel !== undefined
            ? { inheritedLabel: body.inheritedLabel }
            : {}),
          ...(body.inheritedGrants !== undefined
            ? { inheritedGrants: body.inheritedGrants }
            : {}),
          ...(body.task != null ? { task: body.task } : {}),
          ...(body.mcpPins !== undefined ? { mcpPins: body.mcpPins } : {}),
        },
      );
      res.status(HTTP_STATUS.OK).json(spawned);
    } catch (error) {
      if (error instanceof HarnessUnauthorizedError) {
        logAuthorityRefusal(this.logger, req, error);
      }
      next(transportError(error));
    }
  };
}
