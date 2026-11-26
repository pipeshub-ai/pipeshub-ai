import { NextFunction, Request, Response } from 'express';
import { ZodError } from 'zod';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { BudgetLedgerService } from '../application/budget-ledger-service';
import { HarnessLogger, silentHarnessLogger } from '../application/harness-log';
import { HarnessUnauthorizedError } from '../domain/errors';
import { bearer, logAuthorityRefusal } from './internal-auth';
import { transportError } from './internal-controller';
import {
  readBudgetSchema,
  reserveBudgetSchema,
  settleBudgetSchema,
} from './schemas';

/**
 * P6.5 ledger routes under `/internal/runs/:runId/budget`. The service
 * authenticates: the run's gateway token (the model gateway) or its attempt
 * token (the worker's own model calls). Answers are never cached.
 */
export class AgentHarnessBudgetController {
  constructor(
    private readonly budgets: BudgetLedgerService,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  reserve = (req: Request, res: Response, next: NextFunction): void => {
    this.answer(req, res, next, async () => {
      const { params, body } = reserveBudgetSchema.parse(req);
      return this.budgets.reserve(bearer(req), params.runId, body);
    });
  };

  commit = (req: Request, res: Response, next: NextFunction): void => {
    this.answer(req, res, next, async () => {
      const { params, body } = settleBudgetSchema.parse(req);
      if (body.actual === undefined) {
        throw new BadRequestError('A commit names the actual spend');
      }
      return this.budgets.commit(
        bearer(req),
        params.runId,
        params.key,
        body.actual,
        body.usage,
        body.hold !== undefined && body.holdTtlS !== undefined
          ? { amount: body.hold, ttlMs: body.holdTtlS * 1000 }
          : undefined,
        body.modelRole,
      );
    });
  };

  reconcile = (req: Request, res: Response, next: NextFunction): void => {
    this.answer(req, res, next, async () => {
      const { params, body } = settleBudgetSchema.parse(req);
      if (body.actual === undefined || body.hold !== undefined) {
        throw new BadRequestError(
          'A reconcile names the provider-reported spend and holds nothing',
        );
      }
      return this.budgets.reconcile(
        bearer(req),
        params.runId,
        params.key,
        body.actual,
        body.usage,
      );
    });
  };

  release = (req: Request, res: Response, next: NextFunction): void => {
    this.answer(req, res, next, async () => {
      const { params } = settleBudgetSchema.parse(req);
      return this.budgets.release(bearer(req), params.runId, params.key);
    });
  };

  remaining = (req: Request, res: Response, next: NextFunction): void => {
    this.answer(req, res, next, async () => {
      const { params } = readBudgetSchema.parse(req);
      return {
        remaining: await this.budgets.remaining(bearer(req), params.runId),
      };
    });
  };

  private answer(
    req: Request,
    res: Response,
    next: NextFunction,
    work: () => Promise<unknown>,
  ): void {
    work().then(
      (body) => {
        res
          .status(HTTP_STATUS.OK)
          .setHeader('Cache-Control', 'no-store')
          .json(body);
      },
      (error: unknown) => {
        if (error instanceof HarnessUnauthorizedError) {
          logAuthorityRefusal(this.logger, req, error);
        }
        next(
          error instanceof ZodError
            ? new BadRequestError('Invalid budget request')
            : transportError(error),
        );
      },
    );
  }
}
