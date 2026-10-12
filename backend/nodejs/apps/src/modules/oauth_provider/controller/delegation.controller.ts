import { NextFunction, Response } from 'express';
import { injectable, inject } from 'inversify';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { DelegationService } from '../services/delegation.service';

@injectable()
export class DelegationController {
  constructor(
    @inject('DelegationService')
    private readonly delegations: DelegationService,
  ) {}

  async list(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const delegations = await this.delegations.listDelegations(
        this.orgId(req),
        req.query.serviceAccountId as string,
      );
      res.json({ delegations });
    } catch (error) {
      next(error);
    }
  }

  async grant(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const body = req.body as {
        serviceAccountId: string;
        subject: string;
        reason?: string;
      };
      const delegation = await this.delegations.grant(
        this.orgId(req),
        this.userId(req),
        body.serviceAccountId,
        body.subject,
        body.reason,
      );
      res.status(201).json({ delegation });
    } catch (error) {
      next(error);
    }
  }

  async revoke(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      await this.delegations.revoke(
        this.orgId(req),
        this.userId(req),
        req.query.serviceAccountId as string,
        req.params.subjectUserId as string,
      );
      res.status(204).send();
    } catch (error) {
      next(error);
    }
  }

  async listEvents(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const events = await this.delegations.listEvents(this.orgId(req), {
        serviceAccountId: req.query.serviceAccountId as string | undefined,
        limit:
          req.query.limit === undefined ? undefined : Number(req.query.limit),
      });
      res.json({ events });
    } catch (error) {
      next(error);
    }
  }

  private orgId(req: AuthenticatedUserRequest): string {
    const orgId: unknown = req.user?.orgId;
    if (typeof orgId !== 'string' || orgId === '') {
      throw new BadRequestError('Organization not found on request');
    }
    return orgId;
  }

  private userId(req: AuthenticatedUserRequest): string {
    const userId: unknown = req.user?.userId;
    if (typeof userId !== 'string' || userId === '') {
      throw new BadRequestError('User not found on request');
    }
    return userId;
  }
}
