import { NextFunction, Response } from 'express';
import { z, ZodError } from 'zod';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { HarnessAuthority } from '../application/harness-authority';
import { HarnessLogger, silentHarnessLogger } from '../application/harness-log';
import {
  ChatScheduleService,
  type ScheduleToolArguments,
} from '../application/chat-schedules';
import {
  HarnessScheduleRefusedError,
  HarnessUnauthorizedError,
} from '../domain/errors';
import { ZodError as ContractZodError } from 'zod/v3';
import { scheduleCreateArgumentsSchema } from '../contracts/generated/ext/pipeshub/api/schedule-create-arguments';
import { HarnessAttemptRequest, logAuthorityRefusal } from './internal-auth';
import { attemptClaims, transportError } from './internal-controller';
import { scheduleHttpError } from './schedule-routes';

const boundedId = z.string().min(1).max(256);
/**
 * The `schedule_create` tool's arguments (ADR-SCH-01 D2), exactly as the contract the worker validates them by before
 * it asks the person: the digest the person approved covers all of them.
 */
const toolArguments = (args: unknown): ScheduleToolArguments =>
  scheduleCreateArgumentsSchema.parse(args);

export const createChatScheduleSchema = z.object({
  params: z.object({ runId: boundedId }),
  body: z
    .object({
      attemptId: boundedId,
      fence: z.coerce.number().int().nonnegative(),
      interactionId: boundedId,
      toolId: z.string().min(1).max(256),
      arguments: z.unknown(),
      callArgsDigest: z
        .string()
        .regex(/^sha256:[0-9a-f]{64}$/)
        .optional(),
    })
    .strict(),
});

/**
 * The worker's route for a schedule its run's person approved in chat (ADR-SCH-01 D3), attempt token only. The run
 * is the token's; whether the person approved this call, and as which arguments, is read from the run's journal.
 */
export class AgentHarnessChatScheduleController {
  constructor(
    private readonly schedules: ChatScheduleService,
    private readonly authority: HarnessAuthority,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  create = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const { body } = createChatScheduleSchema.parse(req);
      await this.authority.authorizeAttempt(claims, body);
      const made = await this.schedules.create(
        claims.orgId,
        claims.runId,
        claims.attemptId,
        {
          interactionId: body.interactionId,
          toolId: body.toolId,
          arguments: toolArguments(body.arguments),
          ...(body.callArgsDigest !== undefined
            ? { callArgsDigest: body.callArgsDigest }
            : {}),
        },
      );
      res
        .status(made.created ? HTTP_STATUS.CREATED : HTTP_STATUS.OK)
        .json(made);
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  private refused(req: HarnessAttemptRequest, error: unknown): unknown {
    if (error instanceof HarnessUnauthorizedError) {
      logAuthorityRefusal(this.logger, req, error);
    }
    if (error instanceof ZodError || error instanceof ContractZodError) {
      return new BadRequestError('Invalid schedule request');
    }
    if (error instanceof HarnessScheduleRefusedError) {
      return scheduleHttpError(error);
    }
    return transportError(error);
  }
}
