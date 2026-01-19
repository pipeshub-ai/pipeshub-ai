import { injectable, inject } from 'inversify'
import crypto from 'crypto'
import { Request, Response, NextFunction } from 'express'
import { Types } from 'mongoose'
import { OAuthTokenService } from '../services/oauth_token.service'
import { ScopeValidatorService } from '../services/scope.validator.service'
import { OpenIDConfiguration, JWKS, JWK } from '../types/oauth.types'
import { AppConfig } from '../../tokens_manager/config/config'
import { Users } from '../../user_management/schema/users.schema'
import { buildWwwAuthenticateHeader } from '../middlewares/oauth.auth.middleware'

/**
 * OpenID Connect Provider Controller
 *
 * Handles OIDC-specific endpoints:
 * - /.well-known/openid-configuration (Discovery)
 * - /.well-known/jwks.json (JSON Web Key Set)
 * - /userinfo (User Info endpoint)
 *
 * @see https://openid.net/specs/openid-connect-discovery-1_0.html
 * @see https://openid.net/specs/openid-connect-core-1_0.html
 */
@injectable()
export class OIDCProviderController {
  constructor(
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
    @inject('AppConfig') private appConfig: AppConfig,
  ) {}

  /**
   * UserInfo endpoint - GET /oauth2/userinfo
   * @see OpenID Connect Core 1.0 Section 5.3
   * @see RFC 6750 for Bearer Token authentication
   */
  async userInfo(
    req: Request,
    res: Response,
    _next: NextFunction,
  ): Promise<void> {
    try {
      // Extract and verify access token
      const authHeader = req.headers.authorization
      if (!authHeader || !authHeader.startsWith('Bearer ')) {
        // RFC 6750 Section 3.1: Missing token
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

      // Check for required scope
      const scopes = payload.scope.split(' ')
      if (!scopes.includes('openid')) {
        // RFC 6750 Section 3.1: Insufficient scope
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader('insufficient_scope', 'openid scope required', 'openid'),
        )
        res.status(403).json({
          error: 'insufficient_scope',
          error_description: 'openid scope required',
        })
        return
      }

      // Get user info
      const user = await Users.findById(payload.sub)
      if (!user) {
        // RFC 6750 Section 3.1: Invalid token (user not found)
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader('invalid_token', 'User not found'),
        )
        res.status(401).json({
          error: 'invalid_token',
          error_description: 'User not found',
        })
        return
      }

      const userInfo: Record<string, unknown> = {
        sub: (user._id as Types.ObjectId).toString(),
      }

      // Add claims based on scopes
      if (scopes.includes('profile')) {
        userInfo.name = `${user.firstName || ''} ${user.lastName || ''}`.trim()
        userInfo.given_name = user.firstName
        userInfo.family_name = user.lastName
        // Note: profilePictureUrl is not in the User schema
        // updatedAt comes from mongoose timestamps
        const userDoc = user as unknown as { updatedAt?: Date }
        if (userDoc.updatedAt) {
          userInfo.updated_at = Math.floor(userDoc.updatedAt.getTime() / 1000)
        }
      }

      if (scopes.includes('email')) {
        userInfo.email = user.email
        userInfo.email_verified = user.hasLoggedIn
      }

      res.json(userInfo)
    } catch (error) {
      // RFC 6750 Section 3.1: Token validation error
      res.setHeader(
        'WWW-Authenticate',
        buildWwwAuthenticateHeader('invalid_token', (error as Error).message),
      )
      res.status(401).json({
        error: 'invalid_token',
        error_description: (error as Error).message,
      })
    }
  }

  /**
   * OpenID Configuration discovery endpoint
   * GET /.well-known/openid-configuration
   *
   * @see https://openid.net/specs/openid-connect-discovery-1_0.html
   * @see RFC 8414 - OAuth 2.0 Authorization Server Metadata
   */
  async openidConfiguration(
    _req: Request,
    res: Response,
    _next: NextFunction,
  ): Promise<void> {
    const backendUrl = this.appConfig.oauthBackendUrl
    const baseUrl = `${backendUrl}/api/v1/oauth2`

    const config: OpenIDConfiguration = {
      issuer: this.appConfig.oauthIssuer,
      authorization_endpoint: `${baseUrl}/authorize`,
      token_endpoint: `${baseUrl}/token`,
      userinfo_endpoint: `${baseUrl}/userinfo`,
      revocation_endpoint: `${baseUrl}/revoke`,
      introspection_endpoint: `${baseUrl}/introspect`,
      jwks_uri: `${backendUrl}/.well-known/jwks.json`,
      scopes_supported: (() => {
        const scopesByCategory = this.scopeValidatorService.getScopesGroupedByCategory()
        return Object.keys(scopesByCategory).flatMap(
          (cat) => scopesByCategory[cat]?.map((s) => s.name) || [],
        )
      })(),
      response_types_supported: ['code'],
      grant_types_supported: [
        'authorization_code',
        'client_credentials',
        'refresh_token',
      ],
      token_endpoint_auth_methods_supported: [
        'client_secret_basic',
        'client_secret_post',
      ],
      subject_types_supported: ['public'],
      id_token_signing_alg_values_supported: [this.oauthTokenService.getAlgorithm()],
      claims_supported: [
        'sub',
        'iss',
        'aud',
        'exp',
        'iat',
        'name',
        'given_name',
        'family_name',
        'email',
        'email_verified',
        'picture',
      ],
      code_challenge_methods_supported: ['S256', 'plain'],
    }

    res.json(config)
  }

  /**
   * JWKS endpoint - GET /.well-known/jwks.json
   * JSON Web Key Set endpoint
   *
   * Returns the public keys used to verify JWT signatures.
   * Only available when using asymmetric algorithms (RS256).
   * For symmetric algorithms (HS256), returns an empty key set.
   *
   * @see https://datatracker.ietf.org/doc/html/rfc7517
   */
  async jwks(_req: Request, res: Response, _next: NextFunction): Promise<void> {
    const algorithm = this.oauthTokenService.getAlgorithm()

    if (algorithm === 'RS256') {
      const publicKey = this.oauthTokenService.getPublicKey()
      const keyId = this.oauthTokenService.getKeyId()

      if (publicKey && keyId) {
        const jwk = this.publicKeyToJWK(publicKey, keyId)
        const jwks: JWKS = { keys: [jwk] }
        res.json(jwks)
        return
      }
    }

    // For HS256 or if no public key available, return empty JWKS
    res.json({ keys: [] })
  }

  /**
   * Convert PEM public key to JWK format
   */
  private publicKeyToJWK(publicKeyPem: string, kid: string): JWK {
    const publicKeyObject = crypto.createPublicKey(publicKeyPem)
    const jwkExport = publicKeyObject.export({ format: 'jwk' }) as {
      n: string
      e: string
    }

    return {
      kty: 'RSA',
      use: 'sig',
      alg: 'RS256',
      kid,
      n: jwkExport.n,
      e: jwkExport.e,
    }
  }
}
