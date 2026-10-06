import { AuthenticatedUserRequest } from '../middlewares/types';

export interface CallerIdentity {
  readonly userId: string;
  readonly orgId: string;
  readonly authHeaders: Readonly<Record<string, string>>;
  /** Identity of the inbound request; per-request memoization keys on it, so it must be an object. */
  readonly requestKey: object;
}

export function callerIdentityOf(
  req: AuthenticatedUserRequest,
): CallerIdentity {
  return {
    userId: String(req.user?.userId ?? ''),
    orgId: String(req.user?.orgId ?? ''),
    authHeaders: req.headers as Record<string, string>,
    requestKey: req,
  };
}
