import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { AuthMiddleware } from '../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../libs/middlewares/validation.middleware';
import { requireScopes } from '../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../libs/middlewares/types';
import { OAuthScopeNames } from '../../libs/enums/oauth-scopes.enum';
import { ValidationError } from '../../libs/errors/validation.error';
import { getAgentCallRepository } from '../../libs/repositories/agent-call-event';
import type { AgentCallFilter } from '../../libs/repositories/agent-call-event';
import { currentOrgContext } from '../../libs/context/orgContext';
import { userAdminCheck } from '../user_management/middlewares/userAdminCheck';

const MAX_LIMIT = 200;
const DEFAULT_LIMIT = 50;

const isDate = (s: string): boolean => !Number.isNaN(Date.parse(s));

export const listAgentActivitySchema = z.object({
  query: z.object({
    from: z.string().max(40).refine(isDate, 'Invalid date').optional(),
    to: z.string().max(40).refine(isDate, 'Invalid date').optional(),
    userId: z
      .string()
      .regex(/^[0-9a-fA-F]{24}$/, 'Invalid user ID')
      .optional(),
    tokenId: z.string().min(1).max(128).optional(),
    tokenKind: z.enum(['pat', 'oauth', 'delegated']).optional(),
    client: z.string().min(1).max(80).optional(),
    status: z.enum(['2xx', '4xx', '5xx']).optional(),
    cursor: z.string().max(100).optional(),
    limit: z.string().regex(/^\d+$/).optional(),
  }),
});

const encodeCursor = (createdAt: Date, id: string): string =>
  Buffer.from(`${createdAt.getTime()}.${id}`).toString('base64url');

function decodeCursor(cursor: string): { createdAt: Date; id: string } {
  const m = /^(\d{1,15})\.([0-9a-f]{24})$/.exec(
    Buffer.from(cursor, 'base64url').toString(),
  );
  if (!m) throw new ValidationError('Invalid cursor', []);
  return { createdAt: new Date(Number(m[1])), id: m[2] as string };
}

/**
 * GET /api/v1/agent-activity: the calls made with bearer tokens in the caller's organisation, newest first.
 * The organisation is the one on the verified token, never a request field.
 */
export function createAgentActivityRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.get(
    '/',
    (req, res, next) => authMiddleware.authenticate(req, res, next),
    requireScopes(OAuthScopeNames.ORG_ADMIN),
    userAdminCheck,
    ValidationMiddleware.validate(listAgentActivitySchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const q = req.query as z.infer<typeof listAgentActivitySchema>['query'];
        const limit = Math.min(
          Math.max(Number(q.limit ?? DEFAULT_LIMIT), 1),
          MAX_LIMIT,
        );
        const filter: AgentCallFilter = {
          ...(q.from && { from: new Date(q.from) }),
          ...(q.to && { to: new Date(q.to) }),
          ...(q.userId && { userId: q.userId }),
          ...(q.tokenId && { tokenId: q.tokenId }),
          ...(q.tokenKind && { tokenKind: q.tokenKind }),
          ...(q.client && { client: q.client }),
          ...(q.status && { statusClass: q.status }),
          ...(q.cursor && { before: decodeCursor(q.cursor) }),
        };
        const rows = await getAgentCallRepository().list(
          currentOrgContext().orgId as string,
          filter,
          limit + 1,
        );
        const page = rows.slice(0, limit);
        const last = page[page.length - 1];
        res.json({
          items: page.map(({ _id, orgId: _org, ...rest }) => ({
            id: _id,
            ...rest,
          })),
          ...(rows.length > limit &&
            last && { nextCursor: encodeCursor(last.createdAt, last._id) }),
        });
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
