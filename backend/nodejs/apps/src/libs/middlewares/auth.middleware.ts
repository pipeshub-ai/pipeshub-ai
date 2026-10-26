// auth.middleware.ts
import { Response, NextFunction, Request, RequestHandler } from 'express';
import jwt from 'jsonwebtoken';
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import { AuthenticatedServiceRequest, AuthenticatedUserRequest } from './types';
import { AuthTokenService } from '../services/authtoken.service';
import { inject, injectable } from 'inversify';
import { IUserActivity, UserActivities } from '../../modules/auth/schema/userActivities.schema';
import { userActivitiesType } from '../utils/userActivities.utils';
import { findSessionRefusal } from '../utils/user-session.utils';
import { TokenScopes } from '../enums/token-scopes.enum';
import { OAuthTokenService } from '../../modules/oauth_provider/services/oauth_token.service';
import { parseScopeString } from '../../modules/oauth_provider/services/scope.validator.service';
import { Users } from '../../modules/user_management/schema/users.schema';
import { Org } from '../../modules/user_management/schema/org.schema';
import { OAuthApp } from '../../modules/oauth_provider/schema/oauth.app.schema';
import { assertDelegationStillHolds } from '../../modules/oauth_provider/services/delegation-guard';
import { resolveOAuthTokenService } from '../services/oauth-token-service.provider';
import { stripTokenDisplayPrefix } from '../../modules/oauth_provider/constants/constants';
import { makeOrgContext, runWithOrgContext } from '../context/orgContext';

const CLIENT_NAME_HEADER = 'client-name';
const WEB_CLIENT_NAME = 'pipeshub-ai';
export const TOKEN_CLIENT_NAME = 'api';

export type OAuthTokenServiceFactory = () => OAuthTokenService | null;

const { PASSWORD_CHANGED } = userActivitiesType;
// Delay in milliseconds between password change activity and token generation

@injectable()
export class AuthMiddleware {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('AuthTokenService') private tokenService: AuthTokenService,
    private oauthTokenServiceFactory: OAuthTokenServiceFactory = resolveOAuthTokenService,
  ) {
    this.authenticate = this.authenticate.bind(this);
  }

  private isOAuthToken(
    decoded: Record<string, any> | null,
  ): boolean {
    return decoded !== null && decoded.tokenType === 'oauth' && typeof decoded.client_id === 'string' && typeof decoded.iss === 'string';
  }

  async authenticate(
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ) {
    try {
      const token = this.extractToken(req);
      if (!token) {
        throw new UnauthorizedError('No token provided');
      }

      // peek at the token payload to determine token type
      const rawDecoded = jwt.decode(token) as Record<string, any> | null;

      if (!this.isOAuthToken(rawDecoded)) {
        // authenticate regular token
        await this.authenticateRegularToken(token, req);
      } else {
        if (!this.oauthTokenServiceFactory()) {
          throw new UnauthorizedError('OAuth authentication is not configured');
        }
        // authenticate oauth token
        await this.authenticateOAuthToken(token, req);
      }

      // Bind the verified token's org for the rest of the request (never a client field).
      const orgId = req.user?.orgId as string | undefined;
      if (orgId) {
        runWithOrgContext(
          makeOrgContext({
            orgId,
            userId: req.user?.userId ? String(req.user.userId) : null,
            kind: 'user',
            source: 'jwt',
          }),
          next,
        );
      } else {
        next();
      }
    } catch (error) {
      next(error);
    }
  }

  /**
   * Authenticate using a regular token.
   */
  private async authenticateRegularToken(
    token: string,
    req: AuthenticatedUserRequest,
  ): Promise<void> {
    const decoded = await this.tokenService.verifyToken(token);
    req.user = decoded;

    const refusal = await findSessionRefusal(decoded, this.logger);
    if (refusal) {
      throw new UnauthorizedError(refusal);
    }

    this.logger.debug('User authenticated', decoded);
  }

  /**
   * Authenticate using an OAuth access token.
   * Verifies the token, enforces scopes, and populates req.user.
   */
  private async authenticateOAuthToken(
    token: string,
    req: AuthenticatedUserRequest,
  ): Promise<void> {
    const oauthTokenService = this.oauthTokenServiceFactory()!;

    // verify the oauth token (checks signature, expiry, revocation status)
    const payload = await oauthTokenService.verifyAccessToken(token);

    const tokenScopes = parseScopeString(payload.scope);
    if (tokenScopes.length === 0) {
      throw new UnauthorizedError('Token carries no scopes');
    }

    let userId = payload.userId;
    const orgId = payload.orgId;
    let { fullName, accountType } = payload;

    // for client_credentials tokens (userId === client_id), resolve the
    // identity the token acts as.
    //
    // Read from the app record every time, rather than trusting the
    // `createdBy` the token was minted with: the record is the authority on
    // whose documents these tokens reach.
    //
    // A token that disagrees with it is refused below rather than honoured
    // under the identity it was minted with. It is not re-pointed at the
    // current one — pointing an app at a different service account revokes
    // the tokens it has already issued, so a disagreeing claim should not
    // arrive at all.
    const isClientCredentials = userId === payload.client_id;
    if (isClientCredentials) {
      try {
        const app = await OAuthApp.findOne({
          clientId: payload.client_id,
          isDeleted: false,
        })
          .select('createdBy tokenIdentityUserId')
          .lean()
          .exec();
        if (app) {
          // Absent means the creator, which is how every app behaves until
          // someone points it at a service account.
          const resolved = (app.tokenIdentityUserId ?? app.createdBy).toString();

          // The token carries the identity it was minted for. If the
          // application has been pointed somewhere else since, this token is
          // not one of its current credentials and is refused.
          //
          // Substituting the live identity instead would leave the two halves
          // of the product disagreeing: Node would authorise the request as
          // the new identity while the Python services, which read the claim
          // rather than the record, would go on reading as the previous one.
          // Refusing fails both closed, because their role check comes back
          // through here.
          //
          // Revoking on change does not make this unnecessary. A grant that
          // had already loaded the application can insert its row after the
          // revocation has run, and a revocation that throws leaves every
          // existing token carrying the old claim.
          if (
            typeof payload.createdBy === 'string' &&
            payload.createdBy !== resolved
          ) {
            throw new UnauthorizedError(
              'This token was issued for an identity the application no longer acts as',
            );
          }

          userId = resolved;
        } else {
          throw new UnauthorizedError('OAuth app not found or revoked');
        }
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          throw err;
        }
        this.logger.error('Failed to look up OAuth app owner', err);
        throw new UnauthorizedError('Failed to look up OAuth app owner');
      }
    }

    if (!userId) {
      throw new UnauthorizedError('OAuth token missing user identity');
    }

    let email: string | undefined;
    let role: 'admin' | 'member' | undefined;
    const user = await Users.findOne({
      _id: userId,
      orgId: orgId,
      isDeleted: false,
    })
      .select('email fullName role isDisabled kind')
      .lean()
      .exec();

    // Unlike session auth, OAuth/PAT tokens can outlive the user by
    // months or years — a removed employee's token must stop working
    // the same way an expired session would, not just lose its email.
    if (!user) {
      throw new UnauthorizedError('User not found, please login again');
    }

    // Disabling an account has to reach the tokens already issued from it,
    // or it only stops the next sign-in and leaves every outstanding token
    // working. That matters most for a service account, whose whole purpose
    // is to be used by long-lived automation holding a long-lived token.
    if (user.isDisabled) {
      throw new UnauthorizedError('This account is disabled');
    }

    // A delegated token speaks as a person, and the rules that let it be
    // issued have to keep holding for as long as it works — not only at the
    // moment it was signed. Five minutes is plenty of time for a promotion or
    // a switched-off service account to matter, and nothing else here would
    // notice: every check below is about the person named in `userId`, who is
    // a colleague in good standing.
    //
    // These refuse the token outright rather than downgrading it. Clamping
    // `req.user.role` would fix Node and leave the Python services wrong,
    // because they ask `GET /api/v1/users/me/role`, which re-reads the user
    // document (`users.routes.ts:319`) instead of trusting the request. There
    // is no value to clamp that both halves would agree on, so the only
    // answer that fails closed is no token at all.
    if (payload.act?.sub !== undefined) {
      // The rules live in one place because OAuthAuthMiddleware needs the
      // same ones for the OIDC endpoints; see delegation-guard.ts.
      await assertDelegationStillHolds(payload.act.sub, userId, orgId, user);
    }

    email = user.email;
    if (!fullName) {
      fullName = user.fullName;
    }
    // Attach role so Node-side isUserAdmin matches session-JWT behavior
    // (OAuth access tokens do not carry a role claim).
    //
    // A service account is never an admin, whatever its record says. The
    // schema refuses to store that combination, so this is the backstop for a
    // row that predates the rule or was written straight to the database:
    // the guarantee is worth holding at the point the role is actually read,
    // not only at the points it is written.
    role =
      user.role === 'admin' && user.kind !== 'service' ? 'admin' : 'member';

    if (!accountType && isClientCredentials) {
      try {
        const org = await Org.findOne({
          _id: orgId,
          isDeleted: false,
        })
          .select('accountType')
          .lean()
          .exec();
        if (org) {
          accountType = org.accountType;
        }
      } catch (err) {
        this.logger.error('Failed to look up org for OAuth token', err);
        throw new UnauthorizedError('Failed to look up org for OAuth token');
      }
    }

    req.user = {
      userId,
      orgId,
      email,
      fullName,
      accountType,
      role,
      isOAuth: true,
      oauthClientId: payload.client_id,
      oauthScopes: tokenScopes,
    };
    // The web app's client name lets an agent run wait for a person to approve a tool call.
    // An OAuth or personal access token is a program, not the web app, so it can't claim it.
    if (
      String(req.headers[CLIENT_NAME_HEADER] ?? '').toLowerCase() ===
      WEB_CLIENT_NAME
    ) {
      req.headers[CLIENT_NAME_HEADER] = TOKEN_CLIENT_NAME;
    }

    this.logger.debug('OAuth user authenticated', {
      userId,
      orgId,
      clientId: payload.client_id,
      scopes: tokenScopes,
    });
  }

  scopedTokenValidator = (scope: string): RequestHandler => {
    return async (
      req: AuthenticatedServiceRequest,
      _res: Response,
      next: NextFunction,
    ) => {
      try {
        const token = this.extractToken(req);

        if (!token) {
          throw new UnauthorizedError('No token provided');
        }

        const decoded = await this.tokenService.verifyScopedToken(token, scope);
        req.tokenPayload = decoded;
        req.verifiedToken = token;

        const userId = decoded?.userId;
        const orgId = decoded?.orgId as string | undefined;

        this.logger.debug(`userId: ${userId}, orgId: ${orgId}, scope: ${scope}`);

        if (userId && orgId && (scope === TokenScopes.PASSWORD_RESET || scope === TokenScopes.VALIDATE_EMAIL)) {
          let userActivity: Pick<IUserActivity, 'createdAt'> | null = null;
          try {
            userActivity = await UserActivities.findOne({
              userId: userId,
              orgId: orgId,
              isDeleted: false,
              activityType: PASSWORD_CHANGED,
            })
              .sort({ createdAt: -1 }) // sort by most recent first
              .lean()
              .exec();

          } catch (activityError) {
            this.logger.error('Failed to fetch user activity', activityError);
          }

          if (userActivity) {
            const tokenIssuedAt = decoded.iat ? decoded.iat * 1000 : 0;
            const activityTimestamp = userActivity.createdAt?.getTime() || 0;
            if (activityTimestamp > tokenIssuedAt) {
              throw new UnauthorizedError('Password reset link expired, please request for a new link');
            }
          }
        }

        this.logger.debug('User authenticated', decoded);
        // Pre-org flows (login lookups, mail) mint scoped tokens without an orgId; they stay unbound.
        if (orgId) {
          runWithOrgContext(
            makeOrgContext({
              orgId,
              userId: userId ? String(userId) : null,
              kind: 'service',
              source: 'scoped_jwt',
            }),
            next,
          );
        } else {
          next();
        }
      } catch (error) {
        next(error);
      }
    };
  };

  extractToken(req: Request): string | null {
    const authHeader = req.headers.authorization;
    if (!authHeader) return null;

    const [bearer, token] = authHeader.split(' ');
    if (bearer !== 'Bearer' || !token) return null;

    // Personal access tokens and service tokens carry a display-only prefix
    // ahead of the underlying JWT. Strip it here, at the single entry point,
    // so the token-type peek in authenticate() and every downstream verifier
    // see a bare JWT — every other token type never has a prefix, so this is
    // a no-op for them.
    const bare = stripTokenDisplayPrefix(token);
    if (bare === token) return token;
    // Normalise the header too, not just the return value. Several controllers
    // forward req.headers.authorization verbatim to the Python services, which
    // have no notion of the prefix and fail JWT decode on it. Rewriting it here
    // keeps every downstream consumer on a bare JWT.
    req.headers.authorization = `Bearer ${bare}`;
    return bare;
  }
}
