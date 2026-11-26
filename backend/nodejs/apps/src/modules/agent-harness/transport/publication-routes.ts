/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { NextFunction, Request, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  HttpError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  KnowledgePublicationGateway,
  PublicationCaller,
  PublicationRefusedError,
} from '../application/knowledge-publications';
import { AGENT_HARNESS_PUBLICATIONS } from '../composition';
import {
  ERROR_CODES,
  type ErrorCode,
  type ErrorCodeOf,
} from '../contracts/generated/ext/pipeshub/errors';
import { harnessWireCode } from '../domain/errors';
import { transportError } from './errors';
import { identityString } from './request-identity';

export const PUBLICATIONS_PREFIX = '/api/v1/agent-harness/publications';

/** The query service's bound (`MAX_AUDIENCE_ENTRIES`). */
const MAX_AUDIENCE = 500;
const id = z.string().min(1).max(256);

export const createPublicationSchema = z.object({
  body: z
    .object({
      containerKind: z.enum(['kb', 'recordGroup']),
      containerId: id,
      audience: z
        .object({
          userIds: z.array(id).max(MAX_AUDIENCE).default([]),
          teamIds: z.array(id).max(MAX_AUDIENCE).default([]),
          org: z.boolean().default(false),
        })
        .strict()
        .refine(
          (a) => a.org || a.userIds.length + a.teamIds.length > 0,
          'The audience is empty',
        ),
      mode: z.enum(['answer_only', 'full']),
      expiresAt: z.number().int().positive().optional(),
    })
    .strict(),
});

export const listPublicationsSchema = z.object({
  query: z
    .object({ containerKind: z.enum(['kb', 'recordGroup']), containerId: id })
    .strict(),
});

export const publicationParamsSchema = z.object({
  params: z.object({ publicationId: id }),
});

function gateway(container: Container): KnowledgePublicationGateway {
  if (!container.isBound(AGENT_HARNESS_PUBLICATIONS)) {
    throw new ServiceUnavailableError(
      'Knowledge publications are not configured on this deployment',
    );
  }
  return container.get<KnowledgePublicationGateway>(AGENT_HARNESS_PUBLICATIONS);
}

/** The org from the verified credential and the caller's own Authorization header, forwarded as is. */
function caller(req: AuthenticatedUserRequest): PublicationCaller {
  const user = (req as Request & { user?: Record<string, unknown> }).user;
  const orgId = identityString(user?.['orgId']);
  const authorization = req.headers.authorization;
  if (
    orgId === null ||
    typeof authorization !== 'string' ||
    authorization === ''
  ) {
    throw new UnauthorizedError('A publication is managed by a signed-in user');
  }
  return { orgId, authorization };
}

const PUBLICATION_CODES: ReadonlySet<string> = new Set<
  ErrorCodeOf<'publication'>
>(
  (Object.keys(ERROR_CODES) as ErrorCode[]).filter(
    (code): code is ErrorCodeOf<'publication'> =>
      (ERROR_CODES[code] as { readonly group?: string }).group ===
      'publication',
  ),
);

/** A relayed publication refusal as a catalogued code (ERR-UX); one the catalogue lacks is the generic refusal. */
export function publicationHttpError(error: unknown): unknown {
  if (error instanceof PublicationRefusedError) {
    const upper = error.code.toUpperCase();
    const code = (
      PUBLICATION_CODES.has(upper) ? upper : 'PUBLICATION_REFUSED'
    ) as ErrorCodeOf<'publication'>;
    return new HttpError(
      harnessWireCode(code),
      error.message,
      ERROR_CODES[code].status,
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
      next(publicationHttpError(error));
    }
  };

/**
 * Knowledge publications (P2.13, D21): an owner of a knowledge base or record
 * group, or an org admin, discloses it to an audience for answers only or in
 * full, until an expiry; revoking keeps it for audit. The query service
 * decides who may, from the caller's own credential.
 */
export function createPublicationRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');

  router.post(
    '/',
    auth.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    ValidationMiddleware.validate(createPublicationSchema),
    handle(async (req, res) => {
      const body = req.body as z.infer<typeof createPublicationSchema>['body'];
      res.status(201).json(await gateway(container).create(caller(req), body));
    }),
  );

  router.get(
    '/',
    auth.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    ValidationMiddleware.validate(listPublicationsSchema),
    handle(async (req, res) => {
      const query = req.query as unknown as z.infer<
        typeof listPublicationsSchema
      >['query'];
      res.status(200).json({
        publications: await gateway(container).list(
          caller(req),
          query.containerKind,
          query.containerId,
        ),
      });
    }),
  );

  router.delete(
    '/:publicationId',
    auth.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    ValidationMiddleware.validate(publicationParamsSchema),
    handle(async (req, res) => {
      res
        .status(200)
        .json(
          await gateway(container).revoke(
            caller(req),
            String(req.params['publicationId']),
          ),
        );
    }),
  );

  return router;
}
