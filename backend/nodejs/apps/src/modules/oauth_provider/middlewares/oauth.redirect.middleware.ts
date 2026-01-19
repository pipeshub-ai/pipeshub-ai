/**
 * OAuth Redirect Middleware
 *
 * This middleware handles authentication for OAuth endpoints.
 * Instead of returning 401 when a user is not authenticated,
 * it redirects them to the frontend OAuth page which will handle
 * the login flow and redirect back.
 *
 * Flow:
 * 1. User opens /api/v1/oauth2/authorize in browser (no JWT in header)
 * 2. This middleware redirects to frontend /oauth/authorize with same params
 * 3. Frontend checks auth state (JWT in localStorage)
 * 4. If not logged in, frontend redirects to login with returnTo
 * 5. After login, frontend calls backend API with JWT header
 */

import { Request, Response, NextFunction, RequestHandler } from 'express'
import { Logger } from '../../../libs/services/logger.service'
import { AuthTokenService } from '../../../libs/services/authtoken.service'

/**
 * Creates a middleware that redirects to frontend for OAuth authentication
 * @param tokenService - Service to verify JWT tokens
 * @param logger - Logger instance
 * @param frontendUrl - Frontend base URL (e.g., http://localhost:3001)
 */
export function createOAuthRedirectMiddleware(
  tokenService: AuthTokenService,
  logger: Logger,
  frontendUrl: string,
): RequestHandler {
  return async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // Extract token from Authorization header
      const authHeader = req.headers.authorization
      let token: string | null = null

      if (authHeader && authHeader.startsWith('Bearer ')) {
        token = authHeader.substring(7)
      }

      // If no token, redirect to frontend OAuth page
      if (!token) {
        const redirectUrl = buildFrontendOAuthUrl(req, frontendUrl)
        logger.info('No auth token, redirecting to frontend OAuth page', {
          redirectUrl,
        })
        res.redirect(redirectUrl)
        return
      }

      // Verify the token
      try {
        const decoded = await tokenService.verifyToken(token)
        // Map decoded token to user object on request
        // Using type assertion since Express Request.user is extensible
        ;(req as any).user = {
          id: decoded.userId,
          userId: decoded.userId,
          orgId: decoded.orgId,
          email: decoded.email,
          firstName: decoded.firstName,
          lastName: decoded.lastName,
          role: decoded.role,
          accountType: decoded.accountType,
        }
        logger.debug('OAuth request authenticated', { userId: decoded.userId })
        next()
      } catch (tokenError) {
        // Token invalid/expired, redirect to frontend
        logger.info('Token invalid/expired, redirecting to frontend OAuth page')
        const redirectUrl = buildFrontendOAuthUrl(req, frontendUrl)
        res.redirect(redirectUrl)
      }
    } catch (error) {
      next(error)
    }
  }
}

/**
 * Builds the frontend OAuth URL with all query parameters preserved
 */
function buildFrontendOAuthUrl(req: Request, frontendUrl: string): string {
  const url = new URL('/oauth/authorize', frontendUrl)

  // Copy all query parameters
  for (const [key, value] of Object.entries(req.query)) {
    if (typeof value === 'string') {
      url.searchParams.set(key, value)
    }
  }

  return url.toString()
}
