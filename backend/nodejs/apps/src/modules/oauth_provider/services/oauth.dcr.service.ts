/**
 * Dynamic Client Registration (RFC 7591) and Client Configuration
 * (RFC 7592) service.
 *
 * Anonymous wire — the only caller is the registering MCP client. All
 * security gating happens later at /authorize (user login + scope consent)
 * and at the resource server (RFC 8707 audience binding).
 *
 * Public-client semantics, in line with RFC 7591 §3.2.1:
 *   - When `token_endpoint_auth_method === "none"` the response MUST omit
 *     `client_secret` and `client_secret_expires_at`.
 *   - PKCE is enforced in the existing `/authorize` controller for any
 *     client whose `isConfidential === false`.
 */
import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { OAuthAppService } from './oauth.app.service';
import { OAuthTokenService } from './oauth_token.service';
import { ScopeValidatorService } from './scope.validator.service';
import {
  IOAuthApp,
  OAuthAppStatus,
  OAuthGrantType,
  OAuthApplicationType,
  OAuthTokenEndpointAuthMethod,
} from '../schema/oauth.app.schema';
import {
  ClientRegistrationRequest,
  ClientRegistrationResponse,
} from '../types/oauth.types';
import { BadRequestError } from '../../../libs/errors/http.errors';

const DCR_CLIENT_PROFILE = 'mcp.dcr.public';

@injectable()
export class OAuthDcrService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthAppService') private oauthAppService: OAuthAppService,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
    @inject('AppConfig') private appConfig: AppConfig,
  ) {}

  /**
   * RFC 7591 POST /register. Anonymous, idempotent on the server side: each
   * call creates a new client (we deliberately do NOT dedupe by software_id —
   * that's the registrant's job).
   */
  async register(
    body: ClientRegistrationRequest,
  ): Promise<ClientRegistrationResponse> {
    // 1. Defaults per RFC 7591 §2.
    const grantTypes = (body.grant_types ?? [
      OAuthGrantType.AUTHORIZATION_CODE,
      OAuthGrantType.REFRESH_TOKEN,
    ]) as OAuthGrantType[];
    const responseTypes = body.response_types ?? ['code'];
    const tokenEndpointAuthMethod: OAuthTokenEndpointAuthMethod =
      body.token_endpoint_auth_method ?? 'client_secret_basic';

    // client_credentials is forbidden on DCR-registered apps. There's no
    // authenticated identity behind a DCR registration — accepting it would
    // let any anonymous registrant mint app-only tokens against the org's
    // public scope set.
    if (grantTypes.includes(OAuthGrantType.CLIENT_CREDENTIALS)) {
      throw new BadRequestError(
        'invalid_client_metadata: client_credentials grant is not permitted for dynamically registered clients',
      );
    }

    // 2. Cross-validate grant_types ↔ response_types per RFC 7591 §2.1.
    if (grantTypes.includes(OAuthGrantType.AUTHORIZATION_CODE)) {
      if (!responseTypes.includes('code')) {
        throw new BadRequestError(
          'invalid_client_metadata: response_types must include "code" when grant_types includes "authorization_code"',
        );
      }
      // RFC 7591 §3.1: redirect_uris is REQUIRED for authorization_code clients.
      if (!body.redirect_uris || body.redirect_uris.length === 0) {
        throw new BadRequestError(
          'invalid_redirect_uri: redirect_uris is required for authorization_code clients',
        );
      }
    }

    // 3. Resolve scopes — DCR is role-less; cap at the public scope set.
    const requestedScopes = body.scope
      ? this.scopeValidatorService.parseScopes(body.scope)
      : [];
    const publicScopeNames =
      this.scopeValidatorService.getAllowedScopeNamesForRole(false);
    if (requestedScopes.length > 0) {
      this.scopeValidatorService.validateRequestedScopes(
        requestedScopes,
        publicScopeNames,
      );
    }
    // Default to the full public scope set if the client didn't ask for any.
    const allowedScopes =
      requestedScopes.length > 0 ? requestedScopes : publicScopeNames;

    // 4. Generate the registration_access_token up front (RFC 7592).
    const { token: regToken, hash: regTokenHash } =
      this.oauthTokenService.generateRegistrationAccessToken();

    // 5. Persist via the app service.
    const isConfidential = tokenEndpointAuthMethod !== 'none';
    const { app, clientSecret } = await this.oauthAppService.createDcrApp({
      name: body.client_name ?? 'Dynamically registered client',
      redirectUris: body.redirect_uris ?? [],
      allowedGrantTypes: grantTypes,
      allowedScopes,
      isConfidential,
      tokenEndpointAuthMethod,
      responseTypes,
      homepageUrl: body.client_uri,
      logoUrl: body.logo_uri,
      privacyPolicyUrl: body.policy_uri,
      termsOfServiceUrl: body.tos_uri,
      contacts: body.contacts,
      softwareId: body.software_id,
      softwareVersion: body.software_version,
      applicationType: body.application_type as
        | OAuthApplicationType
        | undefined,
      clientProfile: DCR_CLIENT_PROFILE,
      registrationAccessTokenHash: regTokenHash,
    });

    return this.buildResponse(app, body, {
      clientSecret,
      registrationAccessToken: regToken,
    });
  }

  /**
   * RFC 7592 §2.1 GET /register/:client_id — returns the current client metadata.
   * Bearer auth has already been verified by middleware; the app is on the request.
   */
  getRegistrationMetadata(app: IOAuthApp): ClientRegistrationResponse {
    return this.buildResponse(app, {});
  }

  /**
   * RFC 7592 §2.2 PUT /register/:client_id — replaces the client metadata.
   * RFC 7591 ceiling: scopes / grant_types CANNOT be expanded beyond what was
   * originally registered. Our current schema doesn't carry separate ceilings,
   * so we use the existing `allowedScopes` / `allowedGrantTypes` as the cap.
   */
  async updateRegistration(
    app: IOAuthApp,
    body: ClientRegistrationRequest,
  ): Promise<ClientRegistrationResponse> {
    if (body.grant_types) {
      this.oauthAppService.validateGrantTypesPublic(body.grant_types);
      // Same as register(): client_credentials is forbidden for DCR clients,
      // even on update. Defence in depth — a client registered with
      // [authorization_code, refresh_token] cannot upgrade itself.
      if (body.grant_types.includes(OAuthGrantType.CLIENT_CREDENTIALS)) {
        throw new BadRequestError(
          'invalid_client_metadata: client_credentials grant is not permitted for dynamically registered clients',
        );
      }
      const exceeds = body.grant_types.filter(
        (g) => !app.allowedGrantTypes.includes(g as OAuthGrantType),
      );
      if (exceeds.length > 0) {
        throw new BadRequestError(
          `invalid_client_metadata: grant_types not permitted for this client: ${exceeds.join(', ')}`,
        );
      }
      app.allowedGrantTypes = body.grant_types as OAuthGrantType[];
    }

    if (body.redirect_uris) {
      this.oauthAppService.validateRedirectUrisPublic(body.redirect_uris);
      app.redirectUris = body.redirect_uris;
    }

    if (body.scope !== undefined) {
      const requested = this.scopeValidatorService.parseScopes(body.scope);
      this.scopeValidatorService.validateRequestedScopes(requested);
      const exceeds = requested.filter((s) => !app.allowedScopes.includes(s));
      if (exceeds.length > 0) {
        throw new BadRequestError(
          `invalid_client_metadata: scope not permitted for this client: ${exceeds.join(', ')}`,
        );
      }
      app.allowedScopes = requested;
    }

    if (body.client_name !== undefined) app.name = body.client_name;
    if (body.client_uri !== undefined)
      app.homepageUrl = body.client_uri ?? undefined;
    if (body.logo_uri !== undefined) app.logoUrl = body.logo_uri ?? undefined;
    if (body.policy_uri !== undefined)
      app.privacyPolicyUrl = body.policy_uri ?? undefined;
    if (body.tos_uri !== undefined)
      app.termsOfServiceUrl = body.tos_uri ?? undefined;
    if (body.token_endpoint_auth_method !== undefined) {
      app.tokenEndpointAuthMethod = body.token_endpoint_auth_method;
      app.isConfidential = body.token_endpoint_auth_method !== 'none';
    }
    if (body.response_types !== undefined)
      app.responseTypes = body.response_types;
    if (body.contacts !== undefined) app.contacts = body.contacts;
    if (body.software_id !== undefined) app.softwareId = body.software_id;
    if (body.software_version !== undefined)
      app.softwareVersion = body.software_version;
    if (body.application_type !== undefined)
      app.applicationType = body.application_type as OAuthApplicationType;

    await app.save();

    this.logger.info('DCR client metadata updated', { clientId: app.clientId });
    return this.buildResponse(app, body);
  }

  /**
   * RFC 7592 §2.3 DELETE /register/:client_id — soft-deletes the client and
   * revokes every outstanding token issued under it.
   */
  async deleteRegistration(app: IOAuthApp): Promise<void> {
    app.isDeleted = true;
    app.status = OAuthAppStatus.REVOKED;
    app.registrationAccessTokenHash = undefined;
    await app.save();
    await this.oauthTokenService.revokeAllTokensForApp(app.clientId);
    this.logger.info('DCR client deleted', { clientId: app.clientId });
  }

  // ----- internals -----

  private registrationBaseUrl(): string {
    const issuer = this.appConfig.oauthIssuer;
    return `${issuer}/api/v1/oauth2`;
  }

  /**
   * Build the RFC 7591 §3.2.1 response shape.
   *
   * `requestEcho` is the original request body — RFC 7591 says we SHOULD
   * echo every metadata field back (so the registrant sees what we kept vs
   * normalized). When called from RFC 7592 GET we don't have a request, so
   * pass {} and we render from the persisted document only.
   */
  private buildResponse(
    app: IOAuthApp,
    requestEcho: ClientRegistrationRequest,
    overrides?: {
      /** Plaintext client_secret — only present on register() for confidential clients. */
      clientSecret?: string;
      /** Plaintext registration_access_token — only present on register(). */
      registrationAccessToken?: string;
    },
  ): ClientRegistrationResponse {
    const isPublic = !app.isConfidential;
    const clientSecret = overrides?.clientSecret;
    const includeSecret = !isPublic && clientSecret !== undefined;

    const response: ClientRegistrationResponse = {
      // RFC 7591 §3.2.1 server-issued fields
      client_id: app.clientId,
      client_id_issued_at: Math.floor(app.createdAt.getTime() / 1000),
      // Echo request metadata; fall back to persisted values.
      client_name: requestEcho.client_name ?? app.name,
      redirect_uris: requestEcho.redirect_uris ?? app.redirectUris,
      grant_types: requestEcho.grant_types ?? app.allowedGrantTypes,
      response_types: requestEcho.response_types ?? app.responseTypes,
      token_endpoint_auth_method:
        requestEcho.token_endpoint_auth_method ??
        app.tokenEndpointAuthMethod ??
        (isPublic ? 'none' : 'client_secret_basic'),
      scope: (requestEcho.scope ?? app.allowedScopes.join(' ')) || undefined,
      client_uri: requestEcho.client_uri ?? app.homepageUrl,
      logo_uri: requestEcho.logo_uri ?? app.logoUrl,
      policy_uri: requestEcho.policy_uri ?? app.privacyPolicyUrl,
      tos_uri: requestEcho.tos_uri ?? app.termsOfServiceUrl,
      contacts: requestEcho.contacts ?? app.contacts,
      software_id: requestEcho.software_id ?? app.softwareId,
      software_version: requestEcho.software_version ?? app.softwareVersion,
      application_type: requestEcho.application_type ?? app.applicationType,
      client_profile: app.clientProfile,
    };

    if (includeSecret) {
      response.client_secret = clientSecret;
      // 0 = never expires (RFC 7591 §3.2.1).
      response.client_secret_expires_at = 0;
    }

    if (overrides?.registrationAccessToken) {
      response.registration_access_token = overrides.registrationAccessToken;
      response.registration_client_uri = `${this.registrationBaseUrl()}/register/${app.clientId}`;
    } else {
      // On RFC 7592 reads we don't re-issue the token, but we DO surface the URL.
      response.registration_client_uri = `${this.registrationBaseUrl()}/register/${app.clientId}`;
    }

    return response;
  }
}
