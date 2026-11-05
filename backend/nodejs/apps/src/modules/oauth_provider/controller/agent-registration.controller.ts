import { injectable, inject } from 'inversify';
import { Request, Response, NextFunction } from 'express';
import { AgentRegistrationService } from '../services/agent-registration.service';
import { DeviceGrantError } from '../../../libs/errors/oauth.errors';
import { UnauthorizedError } from '../../../libs/errors/http.errors';

interface SessionRequest extends Request {
  user?: { userId: string; orgId: string; email: string };
}

const sessionUser = (
  req: SessionRequest,
): NonNullable<SessionRequest['user']> => {
  if (!req.user) throw new UnauthorizedError('Sign in first');
  return req.user;
};

const STATUS: Record<string, number> = {
  claim_expired: 410,
  access_denied: 403,
};

/** auth.md agent registration endpoints (service_auth). Errors use the OAuth `{ error, error_description }` shape. */
@injectable()
export class AgentRegistrationController {
  constructor(
    @inject('AgentRegistrationService')
    private service: AgentRegistrationService,
  ) {}

  identity = (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const { type, login_hint } = req.body as {
      type?: string;
      login_hint?: string;
    };
    return this.run(res, next, () => this.service.register(type, login_hint));
  };

  claim = (req: Request, res: Response, next: NextFunction): Promise<void> => {
    const { claim_token, email } = req.body as {
      claim_token: string;
      email: string;
    };
    return this.run(res, next, () =>
      this.service.startClaim(claim_token, email),
    );
  };

  claimVerify = (
    req: SessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const { claim_attempt_token } = req.body as { claim_attempt_token: string };
    return this.run(res, next, () =>
      this.service.getClaimContext(claim_attempt_token, sessionUser(req)),
    );
  };

  claimComplete = (
    req: SessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const { claim_attempt_token, user_code, action } = req.body as {
      claim_attempt_token: string;
      user_code?: string;
      action: 'approve' | 'deny';
    };
    return this.run(res, next, async () => ({
      ok: true,
      status: await this.service.completeClaim(
        claim_attempt_token,
        user_code,
        action,
        sessionUser(req),
      ),
    }));
  };

  list = (
    req: SessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> =>
    this.run(res, next, () => this.service.listForUser(sessionUser(req)));

  revoke = (
    req: SessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> =>
    this.run(res, next, () =>
      this.service.revokeForUser(String(req.params.id), sessionUser(req)),
    );

  private async run(
    res: Response,
    next: NextFunction,
    fn: () => Promise<unknown>,
  ): Promise<void> {
    try {
      const body = await fn();
      res.setHeader('Cache-Control', 'no-store');
      res.json(body);
    } catch (error) {
      if (error instanceof DeviceGrantError) {
        res.status(STATUS[error.oauthError] ?? 400).json({
          error: error.oauthError,
          error_description: error.message,
        });
        return;
      }
      next(error);
    }
  }
}
