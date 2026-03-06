/**
 * OpenID Connect Discovery Routes
 *
 * These endpoints MUST be mounted at the root level (/.well-known/*)
 * per RFC 8414 (OAuth 2.0 Authorization Server Metadata)
 *
 * Usage in app.ts:
 *   app.use('/.well-known', createOIDCDiscoveryRouter(container))
 *
 * This exposes:
 *   GET /.well-known/openid-configuration
 *   GET /.well-known/oauth-protected-resource/mcp
 *   GET /.well-known/jwks.json
 */

import { Router, Request, Response, NextFunction } from 'express'
import { Container } from 'inversify'
import { OIDCProviderController } from '../controller/oid.provider.controller'

export function createOIDCDiscoveryRouter(container: Container): Router {
  const router = Router()
  const controller = container.get<OIDCProviderController>('OIDCProviderController')

  /**
   * GET /.well-known/openid-configuration
   * OpenID Connect Discovery endpoint
   *
   * Returns metadata about the OAuth/OIDC provider including:
   * - issuer
   * - authorization_endpoint
   * - token_endpoint
   * - userinfo_endpoint
   * - jwks_uri
   * - scopes_supported
   * - response_types_supported
   * - grant_types_supported
   * - etc.
   *
   * @see https://openid.net/specs/openid-connect-discovery-1_0.html
   * @see RFC 8414 - OAuth 2.0 Authorization Server Metadata
   */
  router.get(
    '/openid-configuration',
    (req: Request, res: Response, next: NextFunction) =>
      controller.openidConfiguration(req, res, next),
  )

  /**
   * GET /.well-known/oauth-protected-resource
   * OAuth Protected Resource Metadata endpoint
   *
   * Per RFC 9728, clients append the resource path to the well-known URL.
   * For example, for a resource at /mcp, clients request:
   *   /.well-known/oauth-protected-resource/mcp
   *
   * @see RFC 9728 - OAuth 2.0 Protected Resource Metadata
   */
  router.get(
    '/oauth-protected-resource/mcp',
    (req: Request, res: Response, next: NextFunction) =>
      controller.oauthProtectedResource(req, res, next),
  )

  /**
   * GET /.well-known/jwks.json
   * JSON Web Key Set endpoint
   *
   * Returns the public keys used to verify JWT signatures.
   * Clients use these keys to verify ID tokens and access tokens.
   *
   * Note: For HS256 (symmetric) signing, this returns empty keys
   * since there are no public keys to expose. For production OIDC,
   * consider using RS256 (asymmetric) signing.
   *
   * @see https://datatracker.ietf.org/doc/html/rfc7517
   */
  router.get(
    '/jwks.json',
    (req: Request, res: Response, next: NextFunction) =>
      controller.jwks(req, res, next),
  )

  return router
}
