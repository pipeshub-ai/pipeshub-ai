import { injectable, inject } from 'inversify'
import { Request, Response, NextFunction } from 'express'
import { Logger } from '../../../libs/services/logger.service'
import { OAuthAppService } from '../services/oauth.app.service'
import { OAuthTokenService } from '../services/oauth_token.service'
import { AuthorizationCodeService } from '../services/authorization_code.service'
import { ScopeValidatorService } from '../services/scope.validator.service'
import {
  InvalidGrantError,
  InvalidClientError,
  UnsupportedGrantTypeError,
  InvalidScopeError,
  AccessDeniedError,
  InvalidRedirectUriError,
} from '../../../libs/errors/oauth.errors'
import {
  AuthorizeRequest,
  TokenRequest,
  TokenResponse,
  ConsentData,
  OAuthErrorResponse,
} from '../types/oauth.types'
import { Users } from '../../user_management/schema/users.schema'
import { Org } from '../../user_management/schema/org.schema'

interface AuthenticatedRequest extends Request {
  user?: {
    userId: string
    orgId: string
    email: string
    fullName?: string
    accountType?: string
  }
}

@injectable()
export class OAuthProviderController {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthAppService') private oauthAppService: OAuthAppService,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('AuthorizationCodeService')
    private authorizationCodeService: AuthorizationCodeService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  /**
   * Authorization endpoint - GET /oauth/authorize
   * Initiates OAuth flow, shows consent page
   * @see RFC 6749 Section 4.1.1 - Authorization Request
   */
  async authorize(
    req: AuthenticatedRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const query = req.query as unknown as AuthorizeRequest

    // RFC 6749 Section 4.1.2.1: MUST validate client_id and redirect_uri
    // BEFORE redirecting any error. If invalid, show error directly.
    let app
    let validatedRedirectUri: string | null = null

    try {
      // Validate client first
      app = await this.oauthAppService.getAppByClientId(query.client_id)

      // Validate redirect URI - MUST be done before any error redirect
      this.oauthAppService.validateRedirectUriForApp(app, query.redirect_uri)
      validatedRedirectUri = query.redirect_uri
    } catch (error) {
      // RFC 6749 Section 4.1.2.1: If redirect_uri is invalid/missing or client_id is invalid,
      // MUST NOT redirect. Display error directly to user.
      if (
        error instanceof InvalidClientError ||
        error instanceof InvalidRedirectUriError
      ) {
        res.status(400).json({
          error: error instanceof InvalidClientError ? 'invalid_client' : 'invalid_request',
          error_description: error.message,
        })
        return
      }
      next(error)
      return
    }

    try {
      // Parse and validate scopes
      const requestedScopes = this.scopeValidatorService.parseScopes(query.scope)
      this.scopeValidatorService.validateScopesForApp(
        requestedScopes,
        app.allowedScopes,
      )

      // RFC 9700: PKCE is REQUIRED for public clients
      if (!app.isConfidential && !query.code_challenge) {
        const redirectUrl = new URL(validatedRedirectUri)
        redirectUrl.searchParams.set('error', 'invalid_request')
        redirectUrl.searchParams.set(
          'error_description',
          'PKCE code_challenge is required for public clients',
        )
        if (query.state) {
          redirectUrl.searchParams.set('state', query.state)
        }
        res.json({ redirectUrl: redirectUrl.toString() })
        return
      }

      // Build consent data
      const user = req.user!
      const scopeDefinitions =
        this.scopeValidatorService.getScopeDefinitions(requestedScopes)

      const consentData: ConsentData = {
        app: {
          name: app.name,
          description: app.description,
          logoUrl: app.logoUrl,
          homepageUrl: app.homepageUrl,
          privacyPolicyUrl: app.privacyPolicyUrl,
        },
        scopes: scopeDefinitions.map((s) => ({
          name: s.name,
          description: s.description,
          category: s.category,
        })),
        user: {
          email: user.email,
          name: user.fullName,
        },
        redirectUri: query.redirect_uri,
        state: query.state,
      }

      // Return consent data for frontend to display
      // The frontend will show a consent page and POST back to /authorize
      res.json({
        requiresConsent: true,
        consentData,
        // Include PKCE params for the POST request
        codeChallenge: query.code_challenge,
        codeChallengeMethod: query.code_challenge_method,
      })
    } catch (error) {
      // Now we can safely redirect errors since redirect_uri is validated
      const errorResponse = this.buildErrorResponse(error as Error, query.state)
      const redirectUrl = new URL(validatedRedirectUri)
      redirectUrl.searchParams.set('error', errorResponse.error)
      if (errorResponse.error_description) {
        redirectUrl.searchParams.set(
          'error_description',
          errorResponse.error_description,
        )
      }
      if (errorResponse.state) {
        redirectUrl.searchParams.set('state', errorResponse.state)
      }
      res.json({ redirectUrl: redirectUrl.toString() })
    }
  }

  /**
   * Authorization consent submission - POST /oauth/authorize
   */
  async authorizeConsent(
    req: AuthenticatedRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const {
        client_id,
        redirect_uri,
        scope,
        state,
        code_challenge,
        code_challenge_method,
        consent, // 'granted' or 'denied'
      } = req.body

      // Validate client
      const app = await this.oauthAppService.getAppByClientId(client_id)

      // Validate redirect URI
      this.oauthAppService.validateRedirectUriForApp(app, redirect_uri)

      // Check consent
      if (consent !== 'granted') {
        const redirectUrl = new URL(redirect_uri)
        redirectUrl.searchParams.set('error', 'access_denied')
        redirectUrl.searchParams.set(
          'error_description',
          'The user denied the authorization request',
        )
        redirectUrl.searchParams.set('state', state)
        res.json({ redirectUrl: redirectUrl.toString() })
        return
      }

      // Parse and validate scopes
      const requestedScopes = this.scopeValidatorService.parseScopes(scope)
      this.scopeValidatorService.validateScopesForApp(
        requestedScopes,
        app.allowedScopes,
      )

      // Generate authorization code
      const user = req.user!
      const code = await this.authorizationCodeService.generateCode(
        client_id,
        user.userId,
        user.orgId,
        redirect_uri,
        requestedScopes,
        code_challenge,
        code_challenge_method,
      )

      // Build redirect URL with code
      const redirectUrl = new URL(redirect_uri)
      redirectUrl.searchParams.set('code', code)
      redirectUrl.searchParams.set('state', state)

      this.logger.info('Authorization code issued', {
        clientId: client_id,
        userId: user.userId,
        scopes: requestedScopes,
      })

      res.json({ redirectUrl: redirectUrl.toString() })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Token endpoint - POST /oauth/token
   */
  async token(
    req: Request,
    res: Response,
    _next: NextFunction,
  ): Promise<void> {
    try {
      const tokenRequest: TokenRequest = req.body

      // Extract client credentials from Authorization header or body
      let clientId = tokenRequest.client_id
      let clientSecret = tokenRequest.client_secret

      const authHeader = req.headers.authorization
      if (authHeader && authHeader.startsWith('Basic ')) {
        const credentials = Buffer.from(
          authHeader.substring(6),
          'base64',
        ).toString('utf-8')
        const colonIndex = credentials.indexOf(':')
        if (colonIndex !== -1) {
          clientId = credentials.substring(0, colonIndex)
          clientSecret = credentials.substring(colonIndex + 1)
        }
      }

      if (!clientId) {
        throw new InvalidClientError('client_id is required')
      }

      let tokenResponse: TokenResponse

      switch (tokenRequest.grant_type) {
        case 'authorization_code':
          tokenResponse = await this.handleAuthorizationCodeGrant(
            clientId,
            clientSecret,
            tokenRequest,
          )
          break

        case 'client_credentials':
          tokenResponse = await this.handleClientCredentialsGrant(
            clientId,
            clientSecret!,
            tokenRequest,
          )
          break

        case 'refresh_token':
          tokenResponse = await this.handleRefreshTokenGrant(
            clientId,
            clientSecret,
            tokenRequest,
          )
          break

        default:
          throw new UnsupportedGrantTypeError(
            `Unsupported grant_type: ${tokenRequest.grant_type}`,
          )
      }

      // Set cache headers as per RFC 6749
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('Pragma', 'no-cache')

      res.json(tokenResponse)
    } catch (error) {
      // OAuth errors should return 400 with JSON body
      const oauthError = this.buildErrorResponse(error as Error)
      res.status(this.getErrorStatusCode(error as Error)).json(oauthError)
    }
  }

  /**
   * Token revocation endpoint - POST /oauth/revoke
   * @see RFC 7009 - OAuth 2.0 Token Revocation
   */
  async revoke(
    req: Request,
    res: Response,
    _next: NextFunction,
  ): Promise<void> {
    try {
      const { token, token_type_hint, client_id, client_secret } = req.body

      // RFC 7009: Verify client credentials
      await this.oauthAppService.verifyClientCredentials(client_id, client_secret)

      // RFC 7009: Revoke token with ownership verification
      // The revokeToken method now validates the token was issued to this client
      await this.oauthTokenService.revokeToken(token, client_id, token_type_hint)

      // RFC 7009: Always return 200 OK, even if token was invalid/already revoked
      res.status(200).send()
    } catch (error) {
      // Even on error, RFC 7009 recommends returning 200 for security
      // Only return error if client authentication fails
      if (error instanceof InvalidClientError) {
        res.status(401).json(this.buildErrorResponse(error))
        return
      }
      res.status(200).send()
    }
  }

  /**
   * Token introspection endpoint - POST /oauth/introspect
   * @see RFC 7662 - OAuth 2.0 Token Introspection
   */
  async introspect(
    req: Request,
    res: Response,
    _next: NextFunction,
  ): Promise<void> {
    try {
      const { token, client_id, client_secret } = req.body

      // RFC 7662: Verify client credentials
      await this.oauthAppService.verifyClientCredentials(client_id, client_secret)

      // RFC 7662: Introspect token with ownership verification
      // The introspectToken method now validates the token was issued to this client
      const result = await this.oauthTokenService.introspectToken(token, client_id)

      res.json(result)
    } catch (error) {
      if (error instanceof InvalidClientError) {
        res.status(401).json(this.buildErrorResponse(error))
        return
      }
      // RFC 7662: For other errors, return inactive token to prevent information disclosure
      res.json({ active: false })
    }
  }

  // Private helper methods

  private async handleAuthorizationCodeGrant(
    clientId: string,
    clientSecret: string | undefined,
    request: TokenRequest,
  ): Promise<TokenResponse> {
    if (!request.code) {
      throw new InvalidGrantError('code is required')
    }
    if (!request.redirect_uri) {
      throw new InvalidGrantError('redirect_uri is required')
    }

    // Get app
    const app = await this.oauthAppService.getAppByClientId(clientId)

    // Verify grant type is allowed
    if (!this.oauthAppService.isGrantTypeAllowed(app, 'authorization_code')) {
      throw new UnsupportedGrantTypeError(
        'authorization_code grant not allowed for this app',
      )
    }

    // Confidential clients must provide client_secret
    if (app.isConfidential) {
      if (!clientSecret) {
        throw new InvalidClientError('client_secret required for confidential clients')
      }
      await this.oauthAppService.verifyClientCredentials(clientId, clientSecret)
    }

    // Exchange code for tokens
    const codeResult = await this.authorizationCodeService.exchangeCode(
      request.code,
      clientId,
      request.redirect_uri,
      request.code_verifier,
    )

    // Look up user details to embed in token
    let fullName: string | undefined
    let accountType: string | undefined
    if (codeResult.userId) {
      const user = await Users.findOne({
        _id: codeResult.userId,
        orgId: codeResult.orgId,
        isDeleted: false,
      })
        .select('fullName')
        .lean()
        .exec()
      if (user) {
        fullName = user.fullName
      }

      const org = await Org.findOne({
        _id: codeResult.orgId,
        isDeleted: false,
      })
        .select('accountType')
        .lean()
        .exec()
      if (org) {
        accountType = (org as any).accountType
      }
    }

    // Generate tokens
    const tokens = await this.oauthTokenService.generateTokens(
      app,
      codeResult.userId,
      codeResult.orgId,
      codeResult.scopes,
      true,
      fullName,
      accountType,
    )

    this.logger.info('Authorization code grant completed', {
      clientId,
      userId: codeResult.userId,
    })

    return {
      access_token: tokens.accessToken,
      token_type: tokens.tokenType,
      expires_in: tokens.expiresIn,
      refresh_token: tokens.refreshToken,
      scope: tokens.scope,
    }
  }

  private async handleClientCredentialsGrant(
    clientId: string,
    clientSecret: string,
    request: TokenRequest,
  ): Promise<TokenResponse> {
    // Verify client credentials
    const app = await this.oauthAppService.verifyClientCredentials(
      clientId,
      clientSecret,
    )

    // Verify grant type is allowed
    if (!this.oauthAppService.isGrantTypeAllowed(app, 'client_credentials')) {
      throw new UnsupportedGrantTypeError(
        'client_credentials grant not allowed for this app',
      )
    }

    // Parse requested scopes
    let scopes = app.allowedScopes
    if (request.scope) {
      const requestedScopes = this.scopeValidatorService.parseScopes(request.scope)
      this.scopeValidatorService.validateScopesForApp(
        requestedScopes,
        app.allowedScopes,
      )
      scopes = requestedScopes
    }

    // Filter out user-specific scopes (openid, profile, email, offline_access)
    const filteredScopes = scopes.filter(
      (s) =>
        !['openid', 'profile', 'email', 'offline_access'].includes(s),
    )

    // Generate tokens (no refresh token for client_credentials)
    const tokens = await this.oauthTokenService.generateTokens(
      app,
      null, // No user
      app.orgId.toString(),
      filteredScopes,
      false, // No refresh token
    )

    this.logger.info('Client credentials grant completed', { clientId })

    return {
      access_token: tokens.accessToken,
      token_type: tokens.tokenType,
      expires_in: tokens.expiresIn,
      scope: tokens.scope,
    }
  }

  private async handleRefreshTokenGrant(
    clientId: string,
    clientSecret: string | undefined,
    request: TokenRequest,
  ): Promise<TokenResponse> {
    if (!request.refresh_token) {
      throw new InvalidGrantError('refresh_token is required')
    }

    // Get app
    const app = await this.oauthAppService.getAppByClientId(clientId)

    // Verify grant type is allowed
    if (!this.oauthAppService.isGrantTypeAllowed(app, 'refresh_token')) {
      throw new UnsupportedGrantTypeError(
        'refresh_token grant not allowed for this app',
      )
    }

    // RFC 6749 Section 6: Confidential clients MUST authenticate
    if (app.isConfidential) {
      if (!clientSecret) {
        throw new InvalidClientError('client_secret required for confidential clients')
      }
      await this.oauthAppService.verifyClientCredentials(clientId, clientSecret)
    }

    // RFC 9700: For public clients, refresh tokens MUST be sender-constrained
    // or use rotation. We implement rotation in oauthTokenService.refreshTokens()
    // which revokes the old token before issuing new ones.

    // Parse requested scopes (can only reduce, not expand)
    const requestedScopes = request.scope
      ? this.scopeValidatorService.parseScopes(request.scope)
      : undefined

    // Refresh tokens (implements token rotation per RFC 9700)
    const tokens = await this.oauthTokenService.refreshTokens(
      app,
      request.refresh_token,
      requestedScopes,
    )

    this.logger.info('Refresh token grant completed', { clientId })

    return {
      access_token: tokens.accessToken,
      token_type: tokens.tokenType,
      expires_in: tokens.expiresIn,
      refresh_token: tokens.refreshToken,
      scope: tokens.scope,
    }
  }

  private buildErrorResponse(
    error: Error,
    state?: string,
  ): OAuthErrorResponse {
    let errorCode = 'server_error'
    let description = error.message

    if (error instanceof InvalidGrantError) {
      errorCode = 'invalid_grant'
    } else if (error instanceof InvalidClientError) {
      errorCode = 'invalid_client'
    } else if (error instanceof UnsupportedGrantTypeError) {
      errorCode = 'unsupported_grant_type'
    } else if (error instanceof InvalidScopeError) {
      errorCode = 'invalid_scope'
    } else if (error instanceof AccessDeniedError) {
      errorCode = 'access_denied'
    }

    const response: OAuthErrorResponse = {
      error: errorCode,
      error_description: description,
    }

    if (state) {
      response.state = state
    }

    return response
  }

  private getErrorStatusCode(error: Error): number {
    if (error instanceof InvalidClientError) return 401
    if (error instanceof AccessDeniedError) return 403
    return 400
  }
}
