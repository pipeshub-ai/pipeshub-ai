/**
 * RFC 7592 registration_access_token authentication middleware.
 *
 * Used to gate GET / PUT / DELETE /register/:client_id. Each registered
 * OAuth app has a sha256 hex hash of its registration_access_token stored
 * on the document; this middleware extracts the bearer token, looks up the
 * app, timing-safe-compares the hash, and attaches the app to the request.
 *
 * On any failure responds RFC 6750 §3.1 401 with a `WWW-Authenticate: Bearer`
 * header — clients distinguish "wrong token" from "wrong client_id" purely
 * by the response code.
 */
import { Request, Response, NextFunction } from 'express'
import crypto from 'crypto'
import { injectable, inject } from 'inversify'
import {
  OAuthApp,
  IOAuthApp,
  OAuthAppRegisteredVia,
} from '../schema/oauth.app.schema'
import { OAuthTokenService } from '../services/oauth_token.service'

export interface RegistrationManagementRequest extends Request {
  oauthApp?: IOAuthApp
}

@injectable()
export class OAuthRegistrationTokenMiddleware {
  constructor(
    @inject('OAuthTokenService') private tokenService: OAuthTokenService,
  ) {
    // Bind so it can be passed straight as an Express handler reference.
    this.authenticate = this.authenticate.bind(this)
  }

  authenticate = async (
    req: RegistrationManagementRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const authHeader = req.headers.authorization
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      this.unauthorized(res, 'Missing bearer token')
      return
    }
    const token = authHeader.substring('Bearer '.length).trim()
    if (!token) {
      this.unauthorized(res, 'Missing bearer token')
      return
    }

    const clientId = req.params.client_id
    if (!clientId) {
      this.unauthorized(res, 'Missing client_id')
      return
    }

    const app = await OAuthApp.findOne({
      clientId: { $eq: clientId },
      isDeleted: false,
      registeredVia: OAuthAppRegisteredVia.DCR,
    })

    if (!app || !app.registrationAccessTokenHash) {
      this.unauthorized(res, 'Invalid registration_access_token')
      return
    }

    const providedHash = this.tokenService.hashTokenPublic(token)
    const storedBuf = Buffer.from(app.registrationAccessTokenHash, 'hex')
    const providedBuf = Buffer.from(providedHash, 'hex')
    if (
      storedBuf.length !== providedBuf.length ||
      !crypto.timingSafeEqual(storedBuf, providedBuf)
    ) {
      this.unauthorized(res, 'Invalid registration_access_token')
      return
    }

    req.oauthApp = app
    next()
  }

  private unauthorized(res: Response, description: string): void {
    res.setHeader(
      'WWW-Authenticate',
      `Bearer error="invalid_token", error_description="${description.replace(
        /"/g,
        '\\"',
      )}"`,
    )
    res.status(401).json({
      error: 'invalid_token',
      error_description: description,
    })
  }
}
