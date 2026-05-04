/**
 * Controller for the public OAuth Dynamic Client Registration endpoints
 * (RFC 7591) and Client Configuration endpoints (RFC 7592).
 *
 * Slim by design — all business logic lives in `OAuthDcrService`. The
 * controller is responsible for:
 *   - Marshaling Express req/res into / out of service calls.
 *   - Setting RFC-mandated cache headers (`Cache-Control: no-store`).
 *   - Mapping known errors onto the RFC 7591 §3.2.2 error response shape.
 */
import { injectable, inject } from 'inversify'
import { Request, Response, NextFunction } from 'express'
import { Logger } from '../../../libs/services/logger.service'
import { OAuthDcrService } from '../services/oauth.dcr.service'
import { ClientRegistrationRequest } from '../types/oauth.types'
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors'
import {
  InvalidRedirectUriError,
  InvalidScopeError,
} from '../../../libs/errors/oauth.errors'
import { RegistrationManagementRequest } from '../middlewares/oauth.registration_token.middleware'

@injectable()
export class OAuthDcrController {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthDcrService') private dcrService: OAuthDcrService,
  ) {}

  /**
   * POST /register — RFC 7591 §3.1. Anonymous; rate-limited.
   * Returns 201 with the full registration response (including freshly-issued
   * client_secret and registration_access_token, if applicable).
   */
  register = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const body = req.body as ClientRegistrationRequest
      const result = await this.dcrService.register(body)
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('Pragma', 'no-cache')
      res.status(201).json(result)
    } catch (error) {
      this.handleError(error, res, next)
    }
  }

  /**
   * GET /register/:client_id — RFC 7592 §2.1. Bearer auth (registration_access_token)
   * already enforced by middleware; the app is on req.oauthApp.
   */
  getRegistration = async (
    req: RegistrationManagementRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const app = req.oauthApp!
      const result = this.dcrService.getRegistrationMetadata(app)
      res.setHeader('Cache-Control', 'no-store')
      res.json(result)
    } catch (error) {
      this.handleError(error, res, next)
    }
  }

  /** PUT /register/:client_id — RFC 7592 §2.2. */
  updateRegistration = async (
    req: RegistrationManagementRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const app = req.oauthApp!
      const body = req.body as ClientRegistrationRequest
      const result = await this.dcrService.updateRegistration(app, body)
      res.setHeader('Cache-Control', 'no-store')
      res.json(result)
    } catch (error) {
      this.handleError(error, res, next)
    }
  }

  /** DELETE /register/:client_id — RFC 7592 §2.3. Returns 204 No Content. */
  deleteRegistration = async (
    req: RegistrationManagementRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const app = req.oauthApp!
      await this.dcrService.deleteRegistration(app)
      res.status(204).send()
    } catch (error) {
      this.handleError(error, res, next)
    }
  }

  /**
   * Map known errors onto RFC 7591 §3.2.2 / §3.2.3 error responses.
   * Anything we don't recognise falls through to the generic error middleware.
   */
  private handleError(error: unknown, res: Response, next: NextFunction): void {
    if (error instanceof InvalidRedirectUriError) {
      res.status(400).json({
        error: 'invalid_redirect_uri',
        error_description: error.message,
      })
      return
    }
    if (error instanceof InvalidScopeError) {
      res.status(400).json({
        error: 'invalid_client_metadata',
        error_description: error.message,
      })
      return
    }
    if (error instanceof BadRequestError) {
      // Service-thrown messages are pre-formatted with the RFC error code prefix
      // (e.g. "invalid_client_metadata: ..."); split them back out.
      const msg = error.message
      const colonIdx = msg.indexOf(':')
      const code =
        colonIdx > 0 && /^[a-z_]+$/.test(msg.slice(0, colonIdx))
          ? msg.slice(0, colonIdx)
          : 'invalid_client_metadata'
      const description =
        colonIdx > 0 ? msg.slice(colonIdx + 1).trim() : msg
      res.status(400).json({ error: code, error_description: description })
      return
    }
    if (error instanceof NotFoundError) {
      res.status(404).json({
        error: 'invalid_client_metadata',
        error_description: error.message,
      })
      return
    }
    this.logger.error('DCR controller error', {
      error: error instanceof Error ? error.message : String(error),
    })
    next(error)
  }
}
