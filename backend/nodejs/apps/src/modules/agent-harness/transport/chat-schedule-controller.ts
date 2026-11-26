import { NextFunction, Response } from 'express';
import { z, ZodError } from 'zod';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { HarnessAuthority } from '../application/harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';
import { ChatScheduleService } from '../application/chat-schedules';
import {
  HarnessScheduleRefusedError,
  HarnessUnauthorizedError,
} from '../domain/errors';
import {
  MAX_SCHEDULE_QUERY_CHARS,
  NOTIFY_CHANNELS,
  type NotifyChannel,
} from '../domain/schedules';
import { HarnessAttemptRequest, logAuthorityRefusal } from './internal-auth';
import { attemptClaims, transportError } from './internal-controller';
import { scheduleHttpError } from './schedule-routes';

const boundedId = z.string().min(1).max(256);
const zone = z.string().min(1).max(64);

/** The `schedule_create` tool's arguments (ADR-SCH-01 D2), exactly: the digest the person approved covers all of them. */
const toolArguments = z
  .object({
    name: z.string().min(1).max(120),
    ask: z.string().min(1).max(MAX_SCHEDULE_QUERY_CHARS),
    when: z.discriminatedUnion('kind', [
      z
        .object({
          kind: z.literal('once'),
          at: z.string().min(16).max(40),
          timeZone: zone,
        })
        .strict(),
      z
        .object({
          kind: z.literal('cron'),
          cron: z.string().min(9).max(120),
          timeZone: zone,
        })
        .strict(),
    ]),
    notify: z
      .array(z.enum(['in_app', 'email', 'slack']))
      .min(1)
      .max(NOTIFY_CHANNELS.length)
      .refine(
        (list): list is NotifyChannel[] => new Set(list).size === list.length,
        'Each channel once',
      )
      .optional(),
  })
  .strict();

export const createChatScheduleSchema = z.object({
  params: z.object({ runId: boundedId }),
  body: z
    .object({
      attemptId: boundedId,
      fence: z.coerce.number().int().nonnegative(),
      interactionId: boundedId,
      toolId: z.string().min(1).max(256),
      arguments: toolArguments,
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
      const made = await this.schedules.create(claims.orgId, claims.runId, {
        interactionId: body.interactionId,
        toolId: body.toolId,
        arguments: body.arguments,
        ...(body.callArgsDigest !== undefined
          ? { callArgsDigest: body.callArgsDigest }
          : {}),
      });
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
    if (error instanceof ZodError) {
      this.logger.warn(
        'schedule.chat_refused',
        harnessLogMeta('schedule.chat_refused', {
          orgId: req.harnessAttempt?.orgId,
          runId: req.harnessAttempt?.runId,
          reason: 'invalid_request',
        }),
      );
      return new BadRequestError('Invalid schedule request');
    }
    if (error instanceof HarnessScheduleRefusedError) {
      this.logger.warn(
        'schedule.chat_refused',
        harnessLogMeta('schedule.chat_refused', {
          orgId: req.harnessAttempt?.orgId,
          runId: req.harnessAttempt?.runId,
          reason: error.code,
        }),
      );
      return scheduleHttpError(error);
    }
    return transportError(error);
  }
}
