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
import { scheduleUpdateArgumentsSchema } from '../contracts/generated/ext/pipeshub/api/schedule-update-arguments';
import { scheduleDeleteArgumentsSchema } from '../contracts/generated/ext/pipeshub/api/schedule-delete-arguments';
import { scheduleFindArgumentsSchema } from '../contracts/generated/ext/pipeshub/api/schedule-find-arguments';
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
      /** The name the model's `call_tool` wrapper named, when it called the tool through it (ADR-SCH-03 D8). */
      calledAs: z.string().min(1).max(256).optional(),
    })
    .strict(),
});

const attemptFields = {
  attemptId: boundedId,
  fence: z.coerce.number().int().nonnegative(),
};
const scheduleParams = z.object({ runId: boundedId, scheduleId: boundedId });

/** ADR-SCH-03 D2: `schedule_find`'s arguments in the query, `madeHere` as text. */
export const findChatSchedulesSchema = z.object({
  params: z.object({ runId: boundedId }),
  query: z
    .object({
      ...attemptFields,
      query: z.string().max(200).optional(),
      status: z.string().max(16).optional(),
      madeHere: z.enum(['true', 'false']).optional(),
    })
    .strict(),
});

export const readChatScheduleSchema = z.object({
  params: scheduleParams,
  query: z.object(attemptFields).strict(),
});

/** ADR-SCH-03 D6: the approved call of `schedule_update` or `schedule_delete`, its `arguments.id` the path's. */
export const changeChatScheduleSchema = z.object({
  params: scheduleParams,
  body: createChatScheduleSchema.shape.body,
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
          ...(body.calledAs !== undefined ? { calledAs: body.calledAs } : {}),
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

  /** ADR-SCH-03 D2: the run's person's own schedules; a read, so no approval. */
  find = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const { query } = findChatSchedulesSchema.parse(req);
      await this.authority.authorizeAttempt(claims, query);
      const args = scheduleFindArgumentsSchema.parse({
        ...(query.query !== undefined ? { query: query.query } : {}),
        ...(query.status !== undefined ? { status: query.status } : {}),
        ...(query.madeHere !== undefined
          ? { madeHere: query.madeHere === 'true' }
          : {}),
      });
      res.status(HTTP_STATUS.OK).json({
        schedules: await this.schedules.find(claims.orgId, claims.runId, args),
      });
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  /** ADR-SCH-03 D5: one schedule's current values, for the card of a change. */
  read = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const { params, query } = readChatScheduleSchema.parse(req);
      await this.authority.authorizeAttempt(claims, query);
      res
        .status(HTTP_STATUS.OK)
        .json(
          await this.schedules.read(
            claims.orgId,
            claims.runId,
            params.scheduleId,
          ),
        );
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  update = this.change('update');
  delete = this.change('delete');

  private change(
    kind: 'update' | 'delete',
  ): (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void> {
    return async (
      req: HarnessAttemptRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        const claims = attemptClaims(req);
        const { params, body } = changeChatScheduleSchema.parse(req);
        await this.authority.authorizeAttempt(claims, body);
        const call = {
          interactionId: body.interactionId,
          toolId: body.toolId,
          ...(body.calledAs !== undefined ? { calledAs: body.calledAs } : {}),
          ...(body.callArgsDigest !== undefined
            ? { callArgsDigest: body.callArgsDigest }
            : {}),
        };
        const done =
          kind === 'update'
            ? await this.schedules.update(
                claims.orgId,
                claims.runId,
                params.scheduleId,
                {
                  ...call,
                  arguments: scheduleUpdateArgumentsSchema.parse(
                    body.arguments,
                  ),
                },
              )
            : await this.schedules.delete(
                claims.orgId,
                claims.runId,
                params.scheduleId,
                {
                  ...call,
                  arguments: scheduleDeleteArgumentsSchema.parse(
                    body.arguments,
                  ),
                },
              );
        res.status(HTTP_STATUS.OK).json(done);
      } catch (error) {
        next(this.refused(req, error));
      }
    };
  }

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
