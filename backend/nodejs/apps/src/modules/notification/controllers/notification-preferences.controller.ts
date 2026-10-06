import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { DomainHttpError } from '../../../libs/errors/domain-http.error';
import {
  INotificationPreferencesRepository,
  NotificationPreferencesPatch,
} from '../repository/notification-preferences.repository';
import { MUTED_SESSIONS_MAX, TipId } from '../schema/user-notification-preferences.schema';
import { resolveNotificationAuthContext } from '../utils/notification-api.utils';

export const MUTED_SESSIONS_LIMIT_CODE = 'MUTED_SESSIONS_LIMIT';

export class MutedSessionsLimitError extends DomainHttpError {
  constructor(max: number) {
    super(
      MUTED_SESSIONS_LIMIT_CODE,
      'The muted conversations limit was reached',
      409,
      { max },
    );
  }
}

type PreferencesHandler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

function withIdentity(
  run: (
    ids: { orgId: string; userId: string },
    req: AuthenticatedUserRequest,
    res: Response,
  ) => Promise<void>,
): PreferencesHandler {
  return async (req, res, next) => {
    try {
      const auth = resolveNotificationAuthContext(req.user);
      if (!auth) {
        res.status(401).json({ message: 'Unauthorized' });
        return;
      }
      await run(
        { orgId: auth.orgOid.toString(), userId: auth.userOid.toString() },
        req,
        res,
      );
    } catch (err) {
      next(err);
    }
  };
}

function sessionIdOf(req: AuthenticatedUserRequest): string {
  const { sessionId } = req.params;
  if (sessionId === undefined) {
    throw new BadRequestError('Session id is required');
  }
  return sessionId;
}

export const getNotificationPreferences = (
  repo: INotificationPreferencesRepository,
): PreferencesHandler =>
  withIdentity(async ({ orgId, userId }, _req, res) => {
    res.json(await repo.get(orgId, userId));
  });

export const updateNotificationPreferences = (
  repo: INotificationPreferencesRepository,
): PreferencesHandler =>
  withIdentity(async ({ orgId, userId }, req, res) => {
    const patch = req.body as NotificationPreferencesPatch;
    res.json(await repo.update(orgId, userId, patch));
  });

export const muteSession = (
  repo: INotificationPreferencesRepository,
): PreferencesHandler =>
  withIdentity(async ({ orgId, userId }, req, res) => {
    const outcome = await repo.muteSession(orgId, userId, sessionIdOf(req));
    if (outcome === 'limit') {
      throw new MutedSessionsLimitError(MUTED_SESSIONS_MAX);
    }
    res.json(await repo.get(orgId, userId));
  });

export const unmuteSession = (
  repo: INotificationPreferencesRepository,
): PreferencesHandler =>
  withIdentity(async ({ orgId, userId }, req, res) => {
    await repo.unmuteSession(orgId, userId, sessionIdOf(req));
    res.json(await repo.get(orgId, userId));
  });

export const markTipSeen = (
  repo: INotificationPreferencesRepository,
): PreferencesHandler =>
  withIdentity(async ({ orgId, userId }, req, res) => {
    const { tipId } = req.body as { tipId: TipId };
    res.json(await repo.markTipSeen(orgId, userId, tipId));
  });
