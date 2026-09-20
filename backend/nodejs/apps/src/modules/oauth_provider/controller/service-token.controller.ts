import { NextFunction, Response } from 'express';
import { injectable, inject } from 'inversify';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { BadRequestError } from '../../../libs/errors/http.errors';
import {
  CreateServiceTokenRequest,
  ServiceTokenService,
} from '../services/service-token.service';

@injectable()
export class ServiceTokenController {
  constructor(
    @inject('ServiceTokenService')
    private readonly serviceTokens: ServiceTokenService,
  ) {}

  async createToken(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const request = req.body as CreateServiceTokenRequest;
      const token = await this.serviceTokens.createToken(
        this.orgId(req),
        this.userId(req),
        request,
      );
      // The raw token appears in this response and nowhere else, ever.
      res.status(201).json(token);
    } catch (error) {
      next(error);
    }
  }

  async listTokens(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const tokens = await this.serviceTokens.listTokens(
        this.orgId(req),
        req.query.serviceAccountId as string,
      );
      res.json(tokens);
    } catch (error) {
      next(error);
    }
  }

  async listScopes(
    _req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      res.json({ scopes: await this.serviceTokens.getAvailableScopes() });
    } catch (error) {
      next(error);
    }
  }

  async revokeToken(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      await this.serviceTokens.revokeToken(
        this.orgId(req),
        this.userId(req),
        req.query.serviceAccountId as string,
        req.params.tokenId as string,
      );
      res.status(204).send();
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
