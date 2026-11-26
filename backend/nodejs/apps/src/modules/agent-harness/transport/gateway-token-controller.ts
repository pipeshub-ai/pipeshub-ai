import { NextFunction, Request, Response } from 'express';
import { ZodError } from 'zod';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { GatewayTokenService } from '../application/gateway-token-service';
import { IssuedGatewayToken } from '../domain/gateway-token';
import { asWorkerNamedOrg, bearer } from './internal-auth';
import { transportError } from './internal-controller';
import {
  attemptGatewayTokenSchema,
  egressAllowedSchema,
  egressVolumeAlertSchema,
  modelFallbackReportSchema,
  secretHiddenReportSchema,
  gatewayRefusalSchema,
  modelGrantSchema,
} from './schemas';

const send = (res: Response, issued: IssuedGatewayToken): void => {
  res
    .status(HTTP_STATUS.OK)
    .setHeader('Cache-Control', 'no-store')
    .json({ token: issued.token, expiresAt: issued.expiresAt });
};

const refused = (error: unknown): unknown =>
  error instanceof ZodError
    ? new BadRequestError('Invalid gateway token request')
    : transportError(error);

/** E2: gateway tokens for the egress proxy, the model gateway's grant, and the gateways' decision reports. */
export class AgentHarnessGatewayTokenController {
  constructor(private readonly tokens: GatewayTokenService) {}

  /** After `requireWorkerCredential`: the deployment's proxy, for the attempt its sandbox serves. */
  forAttempt = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { params, body } = attemptGatewayTokenSchema.parse(req);
      send(
        res,
        await asWorkerNamedOrg(body.orgId, () =>
          this.tokens.forAttempt(
            body.orgId,
            params.runId,
            body.attemptId,
            body.bindingProof,
          ),
        ),
      );
    } catch (error) {
      next(refused(error));
    }
  };

  /**
   * E3: the model gateway presents the run's gateway token itself; no other
   * credential reaches this route. 401 for any token that is not this run's.
   */
  modelGrant = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { params } = modelGrantSchema.parse(req);
      const grant = await this.tokens.modelGrant(bearer(req), params.runId);
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'no-store')
        .json(grant);
    } catch (error) {
      next(refused(error));
    }
  };

  /** SEC2 O-2: the model gateway or the egress proxy, with the run's gateway token. */
  refusal = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { params, body } = gatewayRefusalSchema.parse(req);
      const outcome = await this.tokens.recordRefusal(
        bearer(req),
        params.runId,
        body,
      );
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'no-store')
        .json({ outcome });
    } catch (error) {
      next(refused(error));
    }
  };

  /** OB-03: the egress proxy, with the run's gateway token, for a destination it let through. */
  egressAllowed = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { params, body } = egressAllowedSchema.parse(req);
      const outcome = await this.tokens.recordEgressAllowed(
        bearer(req),
        params.runId,
        body,
      );
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'no-store')
        .json({ outcome });
    } catch (error) {
      next(refused(error));
    }
  };

  /** C6.12: the egress proxy, with the run's gateway token, for an unusual egress volume. */
  egressVolume = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { params, body } = egressVolumeAlertSchema.parse(req);
      const outcome = await this.tokens.recordEgressVolume(
        bearer(req),
        params.runId,
        body,
      );
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'no-store')
        .json({ outcome });
    } catch (error) {
      next(refused(error));
    }
  };

  /** G-48 (WP-100): the model gateway, with the run's gateway token, for a call it moved to an alternate model. */
  modelFallback = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { params, body } = modelFallbackReportSchema.parse(req);
      const outcome = await this.tokens.recordModelFallback(
        bearer(req),
        params.runId,
        body,
      );
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'no-store')
        .json({ outcome });
    } catch (error) {
      next(refused(error));
    }
  };

  /** WP-103: a gateway, with the run's gateway token, for a credential shape it hid from the run's model call. */
  secretHidden = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { params, body } = secretHiddenReportSchema.parse(req);
      const outcome = await this.tokens.recordSecretHidden(
        bearer(req),
        params.runId,
        body,
      );
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'no-store')
        .json({ outcome });
    } catch (error) {
      next(refused(error));
    }
  };
}
