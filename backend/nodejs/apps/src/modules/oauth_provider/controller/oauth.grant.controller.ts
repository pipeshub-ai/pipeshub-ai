import { injectable, inject } from 'inversify';
import { Response, NextFunction } from 'express';
import { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { OAuthGrantService } from '../services/oauth.grant.service';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { UnauthorizedError } from '../../../libs/errors/http.errors';

interface UserContext {
  orgId: string;
  userId: string;
}

interface RequestWithReason {
  reason?: unknown;
}

@injectable()
export class OAuthGrantController {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthGrantService') private oauthGrantService: OAuthGrantService,
  ) {}

  private extractUser(req: AuthenticatedUserRequest): UserContext {
    const user = req.user as unknown as UserContext | undefined;
    if (
      user === undefined ||
      typeof user.orgId !== 'string' ||
      user.orgId.trim() === '' ||
      !Types.ObjectId.isValid(user.orgId) ||
      typeof user.userId !== 'string' ||
      user.userId.trim() === '' ||
      !Types.ObjectId.isValid(user.userId)
    ) {
      throw new UnauthorizedError('User not authenticated');
    }
    return {
      orgId: user.orgId,
      userId: user.userId,
    };
  }

  /**
   * List the calling user's active OAuth grants.
   */
  async listGrants(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { orgId, userId } = this.extractUser(req);
      const grants = await this.oauthGrantService.listUserGrants(orgId, userId);
      res.json({ grants });
    } catch (error) {
      next(error);
    }
  }

  /**
   * Revoke one of the calling user's active OAuth grants.
   */
  async revokeGrant(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { orgId, userId } = this.extractUser(req);
      const grantId = req.params.grantId ?? '';
      const body = req.body as RequestWithReason | undefined;
      const reason = typeof body?.reason === 'string' ? body.reason : undefined;

      await this.oauthGrantService.revokeUserGrant(
        orgId,
        userId,
        grantId,
        reason,
      );

      this.logger.info('OAuth grant revoked via API', {
        orgId,
        userId,
        grantId,
      });

      res.json({ message: 'OAuth grant revoked successfully' });
    } catch (error) {
      next(error);
    }
  }

  /**
   * List every active OAuth grant in the organization across all users.
   * Admin-only.
   */
  async adminListGrants(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { orgId } = this.extractUser(req);
      const page =
        typeof req.query.page === 'number'
          ? req.query.page
          : typeof req.query.page === 'string'
          ? parseInt(req.query.page, 10)
          : undefined;
      const limit =
        typeof req.query.limit === 'number'
          ? req.query.limit
          : typeof req.query.limit === 'string'
          ? parseInt(req.query.limit, 10)
          : undefined;

      const result = await this.oauthGrantService.listAllGrants(
        orgId,
        page,
        limit,
      );
      res.json(result);
    } catch (error) {
      next(error);
    }
  }

  /**
   * Revoke any user's OAuth grant in the organization by id.
   * Admin-only.
   */
  async adminRevokeGrant(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { orgId, userId: adminUserId } = this.extractUser(req);
      const grantId = req.params.grantId ?? '';
      const body = req.body as RequestWithReason | undefined;
      const reason = typeof body?.reason === 'string' ? body.reason : undefined;

      await this.oauthGrantService.adminRevokeGrant(
        orgId,
        adminUserId,
        grantId,
        reason,
      );

      this.logger.info('OAuth grant revoked by admin via API', {
        orgId,
        adminUserId,
        grantId,
      });

      res.json({ message: 'OAuth grant revoked successfully' });
    } catch (error) {
      next(error);
    }
  }
}
