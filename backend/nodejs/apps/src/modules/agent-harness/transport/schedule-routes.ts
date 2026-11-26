/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import express, {
  NextFunction,
  Request,
  RequestHandler,
  Response,
  Router,
} from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  HttpError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  ScheduleCaller,
  ScheduleService,
} from '../application/schedule-service';
import { AGENT_HARNESS_SCHEDULES } from '../composition';
import { localeOf } from './request-locale';
import { ERROR_CODES } from '../contracts/generated/ext/pipeshub/errors';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import {
  HarnessScheduleRefusedError,
  HarnessTriggerRefusedError,
  harnessWireCode,
} from '../domain/errors';
import {
  MAX_COALESCE_S,
  MAX_DEADLINE_S,
  MAX_SCHEDULE_QUERY_CHARS,
  MIN_COALESCE_S,
  MIN_DEADLINE_S,
  NOTIFY_CHANNELS,
  type NotifyChannel,
} from '../domain/schedules';
import { transportError } from './errors';
import {
  identityString,
  KNOWLEDGE_LIMITED_MESSAGE,
  knowledgeLimited,
  requestVia,
} from './request-identity';

export const SCHEDULES_PREFIX = '/api/v1/agent-harness/schedules';
export const TRIGGERS_PREFIX = '/api/v1/agent-harness/triggers';
export const TRIGGER_TOKEN_HEADER = 'x-pipeshub-trigger-token';
export const TRIGGER_SIGNATURE_HEADER = 'x-pipeshub-signature';
export const TRIGGER_TIMESTAMP_HEADER = 'x-pipeshub-timestamp';

const id = z.string().min(1).max(128);
const name = z.string().trim().min(1).max(200);
const idempotencyKey = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9._:~-]+$/);

const template = z
  .object({
    query: z.string().min(1).max(MAX_SCHEDULE_QUERY_CHARS),
    chatMode: z.string().min(1).max(64).optional(),
    tools: z.array(z.string().min(1).max(512)).max(1024).optional(),
    agentCapabilities: z
      .object({
        internalSearch: z.boolean(),
        webSearch: z.boolean(),
        deepSearch: z.boolean().optional(),
      })
      .strict()
      .optional(),
    modelKey: z.string().max(256).optional(),
    modelName: z.string().max(256).optional(),
    modelProvider: z.string().max(128).optional(),
    modelFriendlyName: z.string().max(256).optional(),
    reasoningEffort: z.string().max(64).optional(),
    modelAccountId: z.string().min(1).max(300).optional(),
  })
  .strict();

const coalesceS = z.number().int().min(MIN_COALESCE_S).max(MAX_COALESCE_S);

/** A published version of the schedule's agent, or `latest` to follow every publish (OWD-14). */
const agentVersion = z.union([
  z.number().int().min(1).max(2_147_483_647),
  z.literal('latest'),
]);

const trigger = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('cron'),
      cron: z.string().min(9).max(120),
      timeZone: z.string().min(1).max(64),
      catchUp: z.enum(['skip', 'run_latest', 'run_all']).optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('webhook'),
      coalesceS: coalesceS.optional(),
      auth: z.enum(['signature', 'token']).optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('connector'),
      connectorId: id,
      coalesceS: coalesceS.optional(),
    })
    .strict(),
]);

export const createScheduleSchema = z.object({
  body: z
    .object({
      name,
      agentKey: id.optional(),
      agentVersion: agentVersion.optional(),
      input: template,
      trigger,
      maxCostUsd: z.number().positive().max(1_000).optional(),
      deadlineS: z
        .number()
        .int()
        .min(MIN_DEADLINE_S)
        .max(MAX_DEADLINE_S)
        .optional(),
      overlap: z.enum(['allow', 'skip', 'queue']).optional(),
      notify: z
        .array(z.enum(['in_app', 'email', 'slack']))
        .max(NOTIFY_CHANNELS.length)
        .refine(
          (list): list is NotifyChannel[] => new Set(list).size === list.length,
          'Each channel once',
        )
        .optional(),
    })
    .strict(),
});

export const listSchedulesSchema = z.object({
  query: z
    .object({
      scope: z.enum(['mine']).default('mine'),
      limit: z.coerce.number().int().min(1).max(200).default(50),
    })
    .strict(),
});

export const noticeChannelsSchema = z.object({
  query: z.object({ agentKey: id.optional() }).strict(),
});

export const scheduleParamsSchema = z.object({
  params: z.object({ scheduleId: id }),
});

export const scheduleFiringsSchema = scheduleParamsSchema.extend({
  query: z
    .object({ limit: z.coerce.number().int().min(1).max(200).default(50) })
    .strict(),
});

export const scheduleAgentVersionSchema = scheduleParamsSchema.extend({
  body: z.object({ agentVersion }).strict(),
});

export const runScheduleSchema = scheduleParamsSchema.extend({
  headers: z.object({ 'idempotency-key': idempotencyKey }).loose(),
});

/** Either the signature and its timestamp, or (a webhook made before signatures) the token. */
export const webhookSchema = scheduleParamsSchema.extend({
  headers: z
    .object({
      [TRIGGER_TOKEN_HEADER]: z.string().min(1).max(256).optional(),
      [TRIGGER_SIGNATURE_HEADER]: z.string().min(1).max(512).optional(),
      [TRIGGER_TIMESTAMP_HEADER]: z.string().min(1).max(32).optional(),
    })
    .loose(),
});

/**
 * A webhook's raw body, read before the app's JSON parser so its signature
 * covers the bytes the caller sent; never more than the release accepts
 * (the org's own cap, at most that, is checked once the caller is known).
 */
export function triggerRawBody(): RequestHandler {
  const parse = express.raw({
    type: () => true,
    limit: RELEASE_DEFAULTS.triggers.webhookMaxBytes,
  });
  return (req, res, next) => {
    parse(req, res, (error?: unknown) => {
      next(error === undefined ? undefined : scheduleHttpError(error));
    });
  };
}

export function scheduleHttpError(error: unknown): unknown {
  if (error instanceof HarnessTriggerRefusedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
      error.retryAfterS !== undefined
        ? { retryAfter: String(error.retryAfterS) }
        : undefined,
    );
  }
  if ((error as { type?: unknown } | null)?.type === 'entity.too.large') {
    return new HttpError(
      harnessWireCode('TRIGGER_PAYLOAD_TOO_LARGE'),
      'The webhook body is too large',
      ERROR_CODES.TRIGGER_PAYLOAD_TOO_LARGE.status,
    );
  }
  if (error instanceof HarnessScheduleRefusedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
      error.field !== undefined ? { field: error.field } : undefined,
    );
  }
  return transportError(error);
}

type Handler = (req: AuthenticatedUserRequest, res: Response) => Promise<void>;

const handle =
  (handler: Handler) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      await handler(req, res);
    } catch (error) {
      next(scheduleHttpError(error));
    }
  };

function service(container: Container): ScheduleService {
  if (!container.isBound(AGENT_HARNESS_SCHEDULES)) {
    throw new ServiceUnavailableError(
      'Scheduled runs are not configured on this deployment',
    );
  }
  return container.get<ScheduleService>(AGENT_HARNESS_SCHEDULES);
}

/**
 * Org, user and `via` only from the verified credential, never the body; G-52: the person's locale from the
 * request's `Accept-Language` (the web app sends its UI language), which a schedule made by this request pins.
 * Without the header nothing is pinned and each run answers in its prompt's language (WP100-FU-4): an org-wide
 * default would pin the wrong language for many people in a multilingual org, and a wrong pin is worse than none.
 */
export function scheduleCaller(req: AuthenticatedUserRequest): ScheduleCaller {
  const user = (req as Request & { user?: Record<string, unknown> }).user;
  const orgId = identityString(user?.['orgId']);
  const userId = identityString(user?.['userId']);
  if (orgId === null || userId === null) {
    throw new HarnessScheduleRefusedError(
      'SCHEDULE_FORBIDDEN',
      'A schedule belongs to a signed-in user',
    );
  }
  if (knowledgeLimited(req)) {
    throw new HarnessScheduleRefusedError(
      'SCHEDULE_FORBIDDEN',
      KNOWLEDGE_LIMITED_MESSAGE,
    );
  }
  return { orgId, userId, via: requestVia(req), ...localeOf(req) };
}

const param = (req: Request): string => String(req.params['scheduleId']);

/**
 * Scheduled and triggered runs (P8a). A schedule acts as the user who made
 * it; its actor manages it, an org admin may read, pause, resume or delete it
 * but never run it now.
 */
export function createScheduleRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');

  router.post(
    '/',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(createScheduleSchema),
    handle(async (req, res) => {
      const body = req.body as z.infer<typeof createScheduleSchema>['body'];
      res
        .status(201)
        .json(await service(container).create(scheduleCaller(req), body));
    }),
  );

  router.get(
    '/',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(listSchedulesSchema),
    handle(async (req, res) => {
      const query = req.query as unknown as z.infer<
        typeof listSchedulesSchema
      >['query'];
      res.status(200).json({
        schedules: await service(container).list(
          scheduleCaller(req),
          query.limit,
        ),
      });
    }),
  );

  router.get(
    '/notify-channels',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(noticeChannelsSchema),
    handle(async (req, res) => {
      const query = req.query as unknown as z.infer<
        typeof noticeChannelsSchema
      >['query'];
      res.status(200).json({
        channels: await service(container).noticeChannels(
          scheduleCaller(req),
          query.agentKey,
        ),
      });
    }),
  );

  router.get(
    '/:scheduleId',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(scheduleParamsSchema),
    handle(async (req, res) => {
      res
        .status(200)
        .json(await service(container).get(scheduleCaller(req), param(req)));
    }),
  );

  router.get(
    '/:scheduleId/firings',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(scheduleFiringsSchema),
    handle(async (req, res) => {
      const query = req.query as unknown as z.infer<
        typeof scheduleFiringsSchema
      >['query'];
      res.status(200).json({
        firings: await service(container).firings(
          scheduleCaller(req),
          param(req),
          query.limit,
        ),
      });
    }),
  );

  router.delete(
    '/:scheduleId',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(scheduleParamsSchema),
    handle(async (req, res) => {
      await service(container).delete(scheduleCaller(req), param(req));
      res.status(204).end();
    }),
  );

  for (const action of ['pause', 'resume'] as const) {
    router.post(
      `/:scheduleId/${action}`,
      auth.authenticate,
      requireScopes(OAuthScopeNames.AGENT_EXECUTE),
      ValidationMiddleware.validate(scheduleParamsSchema),
      handle(async (req, res) => {
        res
          .status(200)
          .json(
            await service(container)[action](scheduleCaller(req), param(req)),
          );
      }),
    );
  }

  router.put(
    '/:scheduleId/agent-version',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(scheduleAgentVersionSchema),
    handle(async (req, res) => {
      const body = req.body as z.infer<
        typeof scheduleAgentVersionSchema
      >['body'];
      res
        .status(200)
        .json(
          await service(container).setAgentVersion(
            scheduleCaller(req),
            param(req),
            body.agentVersion,
          ),
        );
    }),
  );

  router.post(
    '/:scheduleId/run',
    auth.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(runScheduleSchema),
    handle(async (req, res) => {
      const key = String(req.headers['idempotency-key']);
      res
        .status(202)
        .json(
          await service(container).runNow(scheduleCaller(req), param(req), key),
        );
    }),
  );

  return router;
}

/**
 * A webhook trigger: no user session, the schedule's own secret instead. A
 * missing schedule and a wrong secret answer alike (401).
 */
export function createTriggerRouter(container: Container): Router {
  const router = Router();
  const header = (req: Request, name: string): string | undefined => {
    const value = req.headers[name];
    return typeof value === 'string' ? value : undefined;
  };
  router.post(
    '/:scheduleId/webhook',
    ValidationMiddleware.validate(webhookSchema),
    handle(async (req, res) => {
      const body = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
      const token = header(req, TRIGGER_TOKEN_HEADER);
      const signature = header(req, TRIGGER_SIGNATURE_HEADER);
      const timestamp = header(req, TRIGGER_TIMESTAMP_HEADER);
      const contentType = header(req, 'content-type');
      res.status(202).json(
        await service(container).webhook(param(req), {
          body,
          ...(token !== undefined ? { token } : {}),
          ...(signature !== undefined ? { signature } : {}),
          ...(timestamp !== undefined ? { timestamp } : {}),
          ...(contentType !== undefined
            ? { contentType: contentType.slice(0, 128) }
            : {}),
        }),
      );
    }),
  );
  return router;
}
