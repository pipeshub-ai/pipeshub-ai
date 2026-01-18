import { Request, Response, NextFunction } from 'express'
import { injectable, inject } from 'inversify'
import { Logger } from '../../../libs/services/logger.service'
import { OAuthTokenService } from '../services/oauth_token.service'
import { ScopeValidatorService } from '../services/scope.validator.service'
import { OAuthTokenPayload } from '../types/oauth.types'
import {
  InvalidTokenError,
  ExpiredTokenError,
} from '../../../libs/errors/oauth.errors'

/**
 * Build RFC 6750 compliant WWW-Authenticate header
 * @see https://datatracker.ietf.org/doc/html/rfc6750#section-3
 */
function buildWwwAuthenticateHeader(
  error?: string,
  errorDescription?: string,
  scope?: string,
  realm: string = 'oauth',
): string {
  const parts = [`Bearer realm="${realm}"`]

  if (error) {
    parts.push(`error="${error}"`)
  }
  if (errorDescription) {
    // Escape backslashes first, then quotes to prevent escape sequence injection
    const safeDescription = errorDescription.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
    parts.push(`error_description="${safeDescription}"`)
  }
  if (scope) {
    parts.push(`scope="${scope}"`)
  }

  return parts.join(', ')
}

export interface OAuthRequest extends Request {
  oauth?: {
    clientId: string
    userId?: string
    orgId: string
    scopes: string[]
    payload: OAuthTokenPayload
  }
}

@injectable()
export class OAuthAuthMiddleware {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  /**
   * Middleware to authenticate OAuth bearer tokens
   * @see RFC 6750 - The OAuth 2.0 Authorization Framework: Bearer Token Usage
   */
  authenticate = async (
    req: OAuthRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const authHeader = req.headers.authorization

      if (!authHeader || !authHeader.startsWith('Bearer ')) {
        // RFC 6750 Section 3.1: If request lacks credentials, return 401 with WWW-Authenticate
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader('invalid_request', 'Bearer token required'),
        )
        res.status(401).json({
          error: 'invalid_request',
          error_description: 'Bearer token required',
        })
        return
      }

      const token = authHeader.substring(7)
      const payload = await this.oauthTokenService.verifyAccessToken(token)

      // Attach OAuth info to request
      req.oauth = {
        clientId: payload.client_id,
        userId: payload.token_type === 'access' ? payload.sub : undefined,
        orgId: payload.org_id,
        scopes: payload.scope.split(' '),
        payload,
      }

      next()
    } catch (error) {
      // RFC 6750 Section 3.1: Always include WWW-Authenticate header on 401
      if (error instanceof ExpiredTokenError) {
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader('invalid_token', 'The access token expired'),
        )
        res.status(401).json({
          error: 'invalid_token',
          error_description: 'The access token expired',
        })
        return
      }

      if (error instanceof InvalidTokenError) {
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader('invalid_token', error.message),
        )
        res.status(401).json({
          error: 'invalid_token',
          error_description: error.message,
        })
        return
      }

      this.logger.error('OAuth authentication error', {
        error: error instanceof Error ? error.message : 'Unknown error',
      })

      res.setHeader(
        'WWW-Authenticate',
        buildWwwAuthenticateHeader('invalid_token', 'Token validation failed'),
      )
      res.status(401).json({
        error: 'invalid_token',
        error_description: 'Token validation failed',
      })
    }
  }

  /**
   * Middleware factory to check for required scopes
   * @see RFC 6750 Section 3.1 - insufficient_scope error
   */
  requireScopes = (...requiredScopes: string[]) => {
    return async (
      req: OAuthRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        if (!req.oauth) {
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader('invalid_token', 'Not authenticated'),
          )
          res.status(401).json({
            error: 'invalid_token',
            error_description: 'Not authenticated',
          })
          return
        }

        const hasAllScopes = this.scopeValidatorService.hasAllScopes(
          req.oauth.scopes,
          requiredScopes,
        )

        if (!hasAllScopes) {
          // RFC 6750 Section 3.1: 403 Forbidden with insufficient_scope error
          const scopeStr = requiredScopes.join(' ')
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader(
              'insufficient_scope',
              `Required scopes: ${scopeStr}`,
              scopeStr,
            ),
          )
          res.status(403).json({
            error: 'insufficient_scope',
            error_description: `Required scopes: ${scopeStr}`,
            scope: scopeStr,
          })
          return
        }

        next()
      } catch (error) {
        this.logger.error('Scope check error', {
          error: error instanceof Error ? error.message : 'Unknown error',
        })

        res.status(500).json({
          error: 'server_error',
          error_description: 'Scope validation failed',
        })
      }
    }
  }

  /**
   * Middleware factory to check for any of the required scopes
   * @see RFC 6750 Section 3.1 - insufficient_scope error
   */
  requireAnyScope = (...requiredScopes: string[]) => {
    return async (
      req: OAuthRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        if (!req.oauth) {
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader('invalid_token', 'Not authenticated'),
          )
          res.status(401).json({
            error: 'invalid_token',
            error_description: 'Not authenticated',
          })
          return
        }

        const hasAnyScope = this.scopeValidatorService.hasAnyScope(
          req.oauth.scopes,
          requiredScopes,
        )

        if (!hasAnyScope) {
          // RFC 6750 Section 3.1: 403 Forbidden with insufficient_scope error
          const scopeStr = requiredScopes.join(' ')
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader(
              'insufficient_scope',
              `One of these scopes required: ${scopeStr}`,
              scopeStr,
            ),
          )
          res.status(403).json({
            error: 'insufficient_scope',
            error_description: `One of these scopes required: ${scopeStr}`,
            scope: scopeStr,
          })
          return
        }

        next()
      } catch (error) {
        this.logger.error('Scope check error', {
          error: error instanceof Error ? error.message : 'Unknown error',
        })

        res.status(500).json({
          error: 'server_error',
          error_description: 'Scope validation failed',
        })
      }
    }
  }
}

// Export the helper for use in controller
export { buildWwwAuthenticateHeader }

/**
 * Create OAuth middleware factory function for use without DI
 */
export function createOAuthMiddleware(
  oauthTokenService: OAuthTokenService,
  scopeValidatorService: ScopeValidatorService,
  logger: Logger,
): OAuthAuthMiddleware {
  const middleware = new OAuthAuthMiddleware(
    logger,
    oauthTokenService,
    scopeValidatorService,
  )
  return middleware
}

/**
 * Standalone scope check function for use in routes
 */
export function scopeCheck(
  requiredScopes: string[],
  oauthTokenService: OAuthTokenService,
  scopeValidatorService: ScopeValidatorService,
) {
  return async (
    req: OAuthRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // First try OAuth token
      const authHeader = req.headers.authorization
      if (authHeader && authHeader.startsWith('Bearer ')) {
        const token = authHeader.substring(7)

        try {
          const payload = await oauthTokenService.verifyAccessToken(token)
          const tokenScopes = payload.scope.split(' ')

          const hasAllScopes = scopeValidatorService.hasAllScopes(
            tokenScopes,
            requiredScopes,
          )

          if (!hasAllScopes) {
            res.status(403).json({
              error: 'insufficient_scope',
              error_description: `Required scopes: ${requiredScopes.join(' ')}`,
            })
            return
          }

          // Attach OAuth info
          req.oauth = {
            clientId: payload.client_id,
            userId: payload.sub,
            orgId: payload.org_id,
            scopes: tokenScopes,
            payload,
          }

          next()
          return
        } catch {
          // Not an OAuth token, continue to next auth method
        }
      }

      // If no OAuth token or invalid, let next middleware handle
      next()
    } catch (error) {
      next(error)
    }
  }
}
