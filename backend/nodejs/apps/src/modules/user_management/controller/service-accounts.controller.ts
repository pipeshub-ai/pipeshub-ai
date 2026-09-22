import { NextFunction, Response } from 'express';
import { injectable, inject } from 'inversify';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { BadRequestError } from '../../../libs/errors/http.errors';
import {
  CreateServiceAccountInput,
  ServiceAccountsService,
  UpdateServiceAccountInput,
} from '../services/service-accounts.service';

@injectable()
export class ServiceAccountsController {
  constructor(
    @inject('ServiceAccountsService')
    private readonly serviceAccounts: ServiceAccountsService,
  ) {}

  async create(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      // The body has already been through the create schema, so the cast
      // narrows a validated value rather than asserting anything new.
      const input = req.body as CreateServiceAccountInput;
      const account = await this.serviceAccounts.create(this.orgId(req), input);
      res.status(201).json(account);
    } catch (error) {
      next(error);
    }
  }

  async list(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      res.json(await this.serviceAccounts.list(this.orgId(req)));
    } catch (error) {
      next(error);
    }
  }

  async get(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const account = await this.serviceAccounts.get(
        this.orgId(req),
        req.params.id as string,
      );
      res.json(account);
    } catch (error) {
      next(error);
    }
  }

  async update(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const input = req.body as UpdateServiceAccountInput;
      const account = await this.serviceAccounts.update(
        this.orgId(req),
        req.params.id as string,
        input,
      );
      res.json(account);
    } catch (error) {
      next(error);
    }
  }

  async remove(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      await this.serviceAccounts.remove(
        this.orgId(req),
        req.params.id as string,
      );
      res.status(204).send();
    } catch (error) {
      next(error);
    }
  }

  /**
   * The organisation always comes from the caller's own token, never from the
   * request, so an administrator of one org cannot reach another org's
   * service accounts by naming it.
   */
  private orgId(req: AuthenticatedUserRequest): string {
    const orgId: unknown = req.user?.orgId;
    if (typeof orgId !== 'string' || orgId === '') {
      throw new BadRequestError('Organization not found on request');
    }
    return orgId;
  }
}
