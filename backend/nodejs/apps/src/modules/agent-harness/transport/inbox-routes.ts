/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  BadRequestError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  INBOX_DEFAULT_LIMIT,
  INBOX_MAX_LIMIT,
  InboxFeedService,
  InboxScheduleNotFoundError,
} from '../application/inbox-feed';
import type { OrgAdminCheck } from '../binders/inbox';
import {
  AGENT_HARNESS_INBOX,
  AGENT_HARNESS_ORG_ADMIN_CHECK,
} from '../binders/tokens';
import {
  decodeNoticeCursor,
  INBOX_FILTERS,
  INBOX_VIEWS,
  isTimeZone,
  noticeIdOf,
  PAUSE_CHOICES,
} from '../domain/inbox';
import type { InboxViewer, NoticeSelection } from '../ports/inbox';
import { transportError } from './errors';
import { identityString, requestVia } from './request-identity';

export const INBOX_PREFIX = '/api/v1/agent-harness/inbox';

/** Notices one action names at most. */
export const MAX_NOTICE_IDS = 100;

const ID = /^[A-Za-z0-9_-]{1,128}$/;

export const inboxFeedSchema = z.object({
  query: z
    .object({
      filter: z.enum(INBOX_FILTERS).optional(),
      view: z.enum(INBOX_VIEWS).optional(),
      cursor: z.string().min(1).max(200).optional(),
      limit: z.coerce.number().int().min(1).max(INBOX_MAX_LIMIT).optional(),
    })
    .strict(),
});

/** UIP3-FU-2: delete archived notices, by id or all of them; a notice that is not archived is left alone. */
export const deleteNoticesSchema = z.object({
  body: z
    .object({
      ids: z
        .array(z.string().regex(/^n:[A-Za-z0-9_-]{1,64}$/))
        .min(1)
        .max(MAX_NOTICE_IDS)
        .optional(),
      all: z.literal(true).optional(),
    })
    .strict()
    .refine(
      (body) => (body.ids !== undefined) !== (body.all !== undefined),
      'Name the archived notices with ids, or send all',
    ),
});

export const inboxCountSchema = z.object({ query: z.object({}).strict() });

export const updateNoticesSchema = z.object({
  body: z
    .object({
      action: z.enum(['read', 'unread', 'archive', 'unarchive', 'restore']),
      ids: z
        .array(z.string().regex(/^n:[A-Za-z0-9_-]{1,64}$/))
        .min(1)
        .max(MAX_NOTICE_IDS)
        .optional(),
      all: z.literal(true).optional(),
      conversationId: z.string().regex(ID).optional(),
    })
    .strict()
    .refine(
      (body) =>
        [body.ids, body.all, body.conversationId].filter(
          (part) => part !== undefined,
        ).length === 1,
      'Name the notices with exactly one of ids, all or conversationId',
    )
    .refine(
      (body) => body.action !== 'unread' || body.ids !== undefined,
      'Mark notices unread by their ids',
    )
    .refine(
      (body) => body.conversationId === undefined || body.action === 'read',
      "A conversation's notices can only be marked read",
    )
    .refine(
      (body) => body.action !== 'restore' || body.all === true,
      'Restore puts every archived notice back: send all',
    ),
});

export const inboxPreferencesReadSchema = z.object({
  query: z.object({}).strict(),
});

export const updateInboxPreferencesSchema = z.object({
  body: z
    .object({
      mute: z
        .object({ scheduleId: z.string().regex(ID), muted: z.boolean() })
        .strict()
        .optional(),
      pause: z
        .object({
          choice: z.enum(PAUSE_CHOICES),
          timeZone: z
            .string()
            .min(1)
            .max(64)
            .refine(isTimeZone, 'Unknown time zone')
            .optional(),
        })
        .strict()
        .optional(),
      runNotices: z
        .object({
          email: z.boolean().optional(),
          slack: z.boolean().optional(),
        })
        .strict()
        .optional(),
    })
    .strict()
    .refine(
      (body) =>
        body.mute !== undefined ||
        body.pause !== undefined ||
        body.runNotices !== undefined,
      'Nothing to change',
    ),
});

/** The caller as the Inbox sees them: org and person from the verified credential, never the request. */
export async function inboxViewer(
  req: AuthenticatedUserRequest,
  container: Container,
): Promise<InboxViewer> {
  const user = req.user as Record<string, unknown> | undefined;
  const orgId = identityString(user?.['orgId']);
  const userId = identityString(user?.['userId']);
  if (orgId === null || userId === null) {
    throw new UnauthorizedError('Authenticated user identity is incomplete');
  }
  const admins = container.isBound(AGENT_HARNESS_ORG_ADMIN_CHECK)
    ? container.get<OrgAdminCheck>(AGENT_HARNESS_ORG_ADMIN_CHECK)
    : null;
  // A service credential acts for no person who reviews the org's proposals.
  const isAdmin =
    requestVia(req) !== 'service' &&
    admins !== null &&
    (await admins(orgId, userId));
  return {
    orgId,
    userId,
    isAdmin,
    ...(typeof req.headers.authorization === 'string'
      ? { authorization: req.headers.authorization }
      : {}),
  };
}

/**
 * One Inbox per person (G2, ADR-INBOX-01): the feed of what waits on them and what happened, the badge's counts,
 * read/unread/archive on notices, and their mutes, pause and finished-run channels. Every route is the caller's own.
 */
export function createInboxRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  const feed = (): InboxFeedService => {
    if (!container.isBound(AGENT_HARNESS_INBOX)) {
      throw new ServiceUnavailableError(
        'The Inbox is not configured on this deployment',
      );
    }
    return container.get<InboxFeedService>(AGENT_HARNESS_INBOX);
  };
  const handle =
    (work: (req: AuthenticatedUserRequest, res: Response) => Promise<void>) =>
    (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): void => {
      work(req, res).catch((error: unknown) => {
        if (error instanceof InboxScheduleNotFoundError) {
          next(new NotFoundError('Schedule not found'));
          return;
        }
        next(transportError(error));
      });
    };

  router.get(
    '/',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(inboxFeedSchema),
    handle(async (req, res) => {
      const { query } = inboxFeedSchema.parse({ query: req.query });
      const cursor =
        query.cursor !== undefined
          ? decodeNoticeCursor(query.cursor)
          : undefined;
      if (cursor === null) throw new BadRequestError('Invalid cursor');
      res.status(200).json(
        await feed().feed(await inboxViewer(req, container), {
          view: query.view ?? 'inbox',
          filter: query.filter ?? 'all',
          ...(cursor !== undefined ? { cursor } : {}),
          limit: query.limit ?? INBOX_DEFAULT_LIMIT,
        }),
      );
    }),
  );

  router.get(
    '/count',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(inboxCountSchema),
    handle(async (req, res) => {
      res
        .status(200)
        .json(await feed().counts(await inboxViewer(req, container)));
    }),
  );

  router.patch(
    '/notices',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(updateNoticesSchema),
    handle(async (req, res) => {
      const { body } = updateNoticesSchema.parse({ body: req.body as unknown });
      const selection: NoticeSelection =
        body.ids !== undefined
          ? { ids: body.ids.flatMap((id) => noticeIdOf(id) ?? []) }
          : body.conversationId !== undefined
            ? { conversationId: body.conversationId }
            : { all: true };
      res
        .status(200)
        .json(
          await feed().apply(
            await inboxViewer(req, container),
            body.action,
            selection,
          ),
        );
    }),
  );

  router.post(
    '/notices/delete',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(deleteNoticesSchema),
    handle(async (req, res) => {
      const { body } = deleteNoticesSchema.parse({ body: req.body as unknown });
      res
        .status(200)
        .json(
          await feed().removeArchived(
            await inboxViewer(req, container),
            body.ids !== undefined
              ? { ids: body.ids.flatMap((id) => noticeIdOf(id) ?? []) }
              : { all: true },
          ),
        );
    }),
  );

  router.get(
    '/preferences',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(inboxPreferencesReadSchema),
    handle(async (req, res) => {
      res
        .status(200)
        .json(await feed().preferences(await inboxViewer(req, container)));
    }),
  );

  router.patch(
    '/preferences',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(updateInboxPreferencesSchema),
    handle(async (req, res) => {
      const { body } = updateInboxPreferencesSchema.parse({
        body: req.body as unknown,
      });
      res.status(200).json(
        await feed().updatePreferences(await inboxViewer(req, container), {
          ...(body.mute !== undefined ? { mute: body.mute } : {}),
          ...(body.pause !== undefined
            ? {
                pause: {
                  choice: body.pause.choice,
                  ...(body.pause.timeZone !== undefined
                    ? { timeZone: body.pause.timeZone }
                    : {}),
                },
              }
            : {}),
          ...(body.runNotices !== undefined
            ? {
                runNotices: {
                  ...(body.runNotices.email !== undefined
                    ? { email: body.runNotices.email }
                    : {}),
                  ...(body.runNotices.slack !== undefined
                    ? { slack: body.runNotices.slack }
                    : {}),
                },
              }
            : {}),
        }),
      );
    }),
  );

  return router;
}
