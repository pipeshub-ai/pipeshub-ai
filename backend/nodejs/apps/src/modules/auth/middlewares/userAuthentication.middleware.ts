import { Response, NextFunction } from 'express';
import { AuthSessionRequest } from './types';
import { SessionService } from '../services/session.service';
import {
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { asAccount } from '../../../libs/context/orgContext';
import { setContextIdentity } from '../../../libs/context/request-context';
import { auditAuthRefusal, authRefusal } from '../utils/sign-in-audit';

export const authSessionMiddleware = async (
  req: AuthSessionRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Auth container not found');
    }
    const sessionService = container.get<SessionService>('SessionService');
    const sessionToken = req.headers['x-session-token'] as string;
    if (!sessionToken) {
      throw authRefusal(
        new UnauthorizedError('Invalid session token'),
        'no_session',
      );
    }
    const session = await sessionService.getSession(sessionToken);
    if (!session) {
      throw authRefusal(
        new UnauthorizedError('Invalid session'),
        'session_expired',
      );
    }
    req.sessionInfo = session;
    // A stored session names the account once the address was matched; before that there is no organisation to bind.
    if (session.orgId && session.userId && session.userId !== 'NOT_FOUND') {
      setContextIdentity({
        orgId: String(session.orgId),
        userId: session.userId,
      });
      return asAccount(String(session.orgId), session.userId, 'session', next);
    }
    next();
  } catch (error) {
    const method: unknown = (req.body as { method?: unknown } | undefined)
      ?.method;
    auditAuthRefusal('auth.sign_in', error, {
      authMethod: typeof method === 'string' ? method : undefined,
    });
    next(error);
  }
};
